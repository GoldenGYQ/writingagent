from __future__ import annotations

import sys
from argparse import Namespace
from unittest.mock import patch

import pytest

from nanobot.desktop.app import (
    _configured_webui_url,
    _gateway_child_command,
    _health_url,
    _run_gateway_child,
    _select_available_port,
    _select_port,
    _webui_url,
)


def test_desktop_urls_are_loopback_only() -> None:
    assert _health_url(8765) == "http://127.0.0.1:8765/health"
    assert _webui_url(8765) == "http://127.0.0.1:8765/"


def test_configured_webui_url_preserves_bootstrap_secret_on_selected_port() -> None:
    configured = "http://127.0.0.1:9999/#/?bootstrapSecret=secret-value"

    with (
        patch("nanobot.cli.runtime_config._load_runtime_config", return_value=object()),
        patch("nanobot.cli.webui_support._webui_browser_url", return_value=configured),
    ):
        result = _configured_webui_url(8766, config=None, workspace=None)

    assert result == "http://127.0.0.1:8766/#/?bootstrapSecret=secret-value"


def test_frozen_gateway_command_reuses_desktop_executable() -> None:
    command = _gateway_child_command(
        8770,
        18795,
        config=r"C:\Users\tester\.nanobot\config.json",
        workspace=r"D:\writing",
        frozen=True,
    )

    assert command == [
        sys.executable,
        "--desktop-gateway-child",
        "--port",
        "8770",
        "--gateway-port",
        "18795",
        "--config",
        r"C:\Users\tester\.nanobot\config.json",
        "--workspace",
        r"D:\writing",
    ]


def test_source_gateway_command_runs_desktop_module() -> None:
    command = _gateway_child_command(
        8765,
        18790,
        config=None,
        workspace=None,
        frozen=False,
    )

    assert command == [
        sys.executable,
        "-m",
        "nanobot.desktop.app",
        "--desktop-gateway-child",
        "--port",
        "8765",
        "--gateway-port",
        "18790",
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


def test_select_available_port_respects_exclusion() -> None:
    with patch("nanobot.desktop.app._port_is_available", return_value=True):
        assert _select_available_port(8765, exclude={8765}) == 8766


def test_gateway_child_uses_desktop_first_run_mode() -> None:
    args = Namespace(port=8765, gateway_port=18790, workspace=None, config=None)

    with (
        patch("nanobot.cli.webui.webui") as webui,
        pytest.raises(SystemExit) as exit_info,
    ):
        _run_gateway_child(args)

    assert exit_info.value.code == 0
    webui.assert_called_once_with(
        port=8765,
        gateway_port=18790,
        workspace=None,
        config=None,
        background=False,
        no_open=True,
        yes=True,
        desktop=True,
    )
