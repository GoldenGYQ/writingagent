from __future__ import annotations

import sys
from unittest.mock import patch

from nanobot.desktop.app import _gateway_child_command, _health_url, _select_port, _webui_url


def test_desktop_urls_are_loopback_only() -> None:
    assert _health_url(8765) == "http://127.0.0.1:8765/health"
    assert _webui_url(8765) == "http://127.0.0.1:8765/"


def test_frozen_gateway_command_reuses_desktop_executable() -> None:
    command = _gateway_child_command(
        8770,
        config=r"C:\Users\tester\.nanobot\config.json",
        workspace=r"D:\writing",
        frozen=True,
    )

    assert command == [
        sys.executable,
        "--desktop-gateway-child",
        "--port",
        "8770",
        "--config",
        r"C:\Users\tester\.nanobot\config.json",
        "--workspace",
        r"D:\writing",
    ]


def test_source_gateway_command_runs_desktop_module() -> None:
    command = _gateway_child_command(8765, config=None, workspace=None, frozen=False)

    assert command == [
        sys.executable,
        "-m",
        "nanobot.desktop.app",
        "--desktop-gateway-child",
        "--port",
        "8765",
    ]


def test_select_port_attaches_to_existing_gateway() -> None:
    with patch("nanobot.desktop.app._is_gateway_healthy", return_value=True):
        assert _select_port(8765) == (8765, True)


def test_select_port_skips_an_occupied_non_gateway_port() -> None:
    with (
        patch("nanobot.desktop.app._is_gateway_healthy", return_value=False),
        patch("nanobot.desktop.app._port_is_available", side_effect=[False, True]),
    ):
        assert _select_port(8765) == (8766, False)
