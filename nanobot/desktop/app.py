"""Windows desktop launcher backed by the existing nanobot WebUI gateway."""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import suppress
from pathlib import Path
from typing import IO, NoReturn

from nanobot import __version__
from nanobot.config.paths import get_logs_dir

APP_NAME = "JLU Writing Agent"
DEFAULT_WEBUI_PORT = 8765
DEFAULT_GATEWAY_PORT = 18790
PORT_SCAN_LIMIT = 30
STARTUP_TIMEOUT_SECONDS = 60.0


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _health_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/health"


def _webui_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/"


def _configured_webui_url(
    port: int | None,
    *,
    config: str | None,
    workspace: str | None,
) -> str:
    """Return the authenticated WebUI URL written by the gateway child."""
    from nanobot.cli.runtime_config import _load_runtime_config
    from nanobot.cli.webui_support import _webui_browser_url

    loaded = _load_runtime_config(config, workspace)
    configured_url = _webui_browser_url(loaded)
    if port is None:
        return configured_url
    expected_origin = _webui_url(port).rstrip("/")
    marker = "/#/"
    if marker in configured_url:
        return f"{expected_origin}{marker}{configured_url.split(marker, 1)[1]}"
    return _webui_url(port)


def _is_gateway_healthy(port: int, *, timeout: float = 0.5) -> bool:
    try:
        with urllib.request.urlopen(_health_url(port), timeout=timeout) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def _port_is_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _select_port(preferred_port: int) -> tuple[int, bool]:
    """Return ``(port, attached)`` for an existing or new local gateway."""
    if _is_gateway_healthy(preferred_port):
        return preferred_port, True
    for port in range(preferred_port, preferred_port + PORT_SCAN_LIMIT):
        if _port_is_available(port):
            return port, False
    raise RuntimeError("No available local port was found for the desktop gateway.")


def _select_available_port(preferred_port: int, *, exclude: set[int] | None = None) -> int:
    excluded = exclude or set()
    for port in range(preferred_port, preferred_port + PORT_SCAN_LIMIT):
        if port not in excluded and _port_is_available(port):
            return port
    raise RuntimeError("No available local port was found for the desktop WebUI.")


def _gateway_child_command(
    port: int,
    gateway_port: int,
    *,
    config: str | None,
    workspace: str | None,
    frozen: bool | None = None,
) -> list[str]:
    frozen = _is_frozen() if frozen is None else frozen
    if frozen:
        command = [sys.executable, "--desktop-gateway-child"]
    else:
        command = [sys.executable, "-m", "nanobot.desktop.app", "--desktop-gateway-child"]
    command.extend(["--port", str(port), "--gateway-port", str(gateway_port)])
    if config:
        command.extend(["--config", config])
    if workspace:
        command.extend(["--workspace", workspace])
    return command


def _open_gateway_log(config: str | None) -> IO[bytes]:
    if config:
        log_dir = Path(config).expanduser().resolve(strict=False).parent / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
    else:
        log_dir = get_logs_dir()
    log_path = log_dir / "desktop-gateway.log"
    return log_path.open("ab", buffering=0)


def _spawn_gateway(
    port: int,
    gateway_port: int,
    *,
    config: str | None,
    workspace: str | None,
) -> subprocess.Popen[bytes]:
    command = _gateway_child_command(
        port,
        gateway_port,
        config=config,
        workspace=workspace,
    )
    creation_flags = 0
    startup_info: subprocess.STARTUPINFO | None = None
    if sys.platform == "win32":
        creation_flags = subprocess.CREATE_NO_WINDOW
        startup_info = subprocess.STARTUPINFO()
        startup_info.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    environment = os.environ.copy()
    environment.setdefault("PYTHONUTF8", "1")
    log_file = _open_gateway_log(config)
    try:
        return subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=environment,
            creationflags=creation_flags,
            startupinfo=startup_info,
        )
    finally:
        log_file.close()


def _wait_for_gateway(process: subprocess.Popen[bytes], port: int) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        return_code = process.poll()
        if return_code is not None:
            raise RuntimeError(
                f"The local gateway exited during startup (exit code {return_code})."
            )
        if _is_gateway_healthy(port):
            return
        time.sleep(0.2)
    raise RuntimeError("The local gateway did not become ready within 60 seconds.")


def _stop_gateway(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _show_fatal_error(message: str) -> None:
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(  # pyright: ignore[reportAttributeAccessIssue]
            0,
            message,
            APP_NAME,
            0x10,
        )
        return
    print(f"{APP_NAME}: {message}", file=sys.stderr)


def _run_gateway_child(args: argparse.Namespace) -> NoReturn:
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            with suppress(Exception):
                reconfigure(encoding="utf-8", errors="replace")

    from nanobot.cli.webui import webui

    webui(
        port=args.port,
        gateway_port=args.gateway_port,
        workspace=args.workspace,
        config=args.config,
        background=False,
        no_open=True,
        yes=True,
        desktop=True,
    )
    raise SystemExit(0)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=APP_NAME)
    parser.add_argument("--desktop-gateway-child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, default=DEFAULT_WEBUI_PORT)
    parser.add_argument("--gateway-port", type=int, default=DEFAULT_GATEWAY_PORT)
    parser.add_argument("--config")
    parser.add_argument("--workspace")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def run_desktop(args: argparse.Namespace) -> int:
    gateway_process: subprocess.Popen[bytes] | None = None
    try:
        gateway_port, attached = _select_port(args.gateway_port)
        webui_port: int | None = None
        if not attached:
            webui_port = _select_available_port(args.port, exclude={gateway_port})
            gateway_process = _spawn_gateway(
                webui_port,
                gateway_port,
                config=args.config,
                workspace=args.workspace,
            )
            _wait_for_gateway(gateway_process, gateway_port)

        import webview  # pyright: ignore[reportMissingImports, reportMissingTypeStubs]

        webview.create_window(  # pyright: ignore[reportUnknownMemberType]
            APP_NAME,
            _configured_webui_url(
                webui_port,
                config=args.config,
                workspace=args.workspace,
            ),
            width=1440,
            height=900,
            min_size=(1080, 680),
            background_color="#F8FAFC",
        )
        webview.start(  # pyright: ignore[reportUnknownMemberType]
            gui="edgechromium" if sys.platform == "win32" else None,
            private_mode=False,
        )
        return 0
    except Exception as exc:
        _show_fatal_error(
            f"The desktop application could not start.\n\n{exc}\n\n"
            f"Details are available in {get_logs_dir() / 'desktop-gateway.log'}."
        )
        return 1
    finally:
        _stop_gateway(gateway_process)


def main() -> int:
    args = _build_parser().parse_args()
    if args.desktop_gateway_child:
        _run_gateway_child(args)
    return run_desktop(args)


if __name__ == "__main__":
    raise SystemExit(main())
