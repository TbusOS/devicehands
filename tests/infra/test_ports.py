"""Default ports: one table (infra.ports), every reader uses it.

DeviceHands has to run on the same computer as the program it grew out of,
which keeps 8765 / 5037 / 9001 / 8731. A reader that still had its own copy
of an old number would silently talk to the other program: a CLI pointed at
the other hub, a serial client reading the other hub's UART.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from devicehands.infra import ports

_ROOT = Path(__file__).resolve().parents[2]
_BENCH = _ROOT / "clients" / "windows-agent" / "alb_agent.py"

_OLD = {8765, 5037, 9001, 8731}


def test_new_defaults_do_not_reuse_any_old_port() -> None:
    new = {
        ports.DEFAULT_API_PORT,
        ports.DEFAULT_ADB_FORWARD_PORT,
        ports.DEFAULT_SERIAL_FORWARD_PORT,
        ports.DEFAULT_BENCH_STATUS_PORT,
    }
    assert new == {8775, 8776, 8777, 8741}
    assert not new & _OLD
    # The adb forwarder no longer sits on adb's own port.
    assert ports.DEFAULT_ADB_FORWARD_PORT != ports.ADB_SERVER_DEFAULT_PORT


def test_hub_listens_on_the_default_api_port() -> None:
    from devicehands.api import server

    assert server.DEFAULT_API_PORT == ports.DEFAULT_API_PORT


def test_forwarders_and_serial_client_share_the_defaults() -> None:
    from devicehands.infra.config import SerialConfig
    from devicehands.remote import forwarder

    assert forwarder.DEFAULT_ADB_PORT == ports.DEFAULT_ADB_FORWARD_PORT
    assert forwarder.DEFAULT_SERIAL_PORT == ports.DEFAULT_SERIAL_FORWARD_PORT
    # The serial client with nothing configured must reach the forwarder
    # of a hub on the same machine.
    assert SerialConfig().default_tcp_port == forwarder.DEFAULT_SERIAL_PORT


def test_forwarder_status_reports_the_default_ports(monkeypatch: pytest.MonkeyPatch) -> None:
    from devicehands.remote import forwarder

    monkeypatch.delenv("DHANDS_ADB_FORWARD_PORT", raising=False)
    monkeypatch.delenv("DHANDS_SERIAL_FORWARD_PORT", raising=False)
    st = forwarder.forwarder_status()
    assert st["adb"]["port"] == 8776
    assert st["serial"]["port"] == 8777


def test_forwarder_ports_can_still_be_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    from devicehands.remote import forwarder

    monkeypatch.setenv("DHANDS_ADB_FORWARD_PORT", "18776")
    monkeypatch.setenv("DHANDS_SERIAL_FORWARD_PORT", "18777")
    st = forwarder.forwarder_status()
    assert (st["adb"]["port"], st["serial"]["port"]) == (18776, 18777)


@pytest.mark.parametrize(
    ("env", "want"),
    [
        ({}, "http://127.0.0.1:8775"),
        ({"DHANDS_API_PORT": "9999"}, "http://127.0.0.1:9999"),
        ({"DHANDS_API_HOST": "0.0.0.0"}, "http://127.0.0.1:8775"),
        ({"DHANDS_API_URL": "http://hub.example:1234/"}, "http://hub.example:1234"),
    ],
)
def test_cli_finds_the_hub_on_the_default_port(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], want: str
) -> None:
    """Both places that build the hub address (flash / bench CLI and the adb
    endpoint discovery) agree, with and without overrides."""
    from devicehands.cli.flash_cli import hub_url
    from devicehands.infra.adb_endpoint import _hub_base_url

    for var in ("DHANDS_API_URL", "DHANDS_API_HOST", "DHANDS_API_PORT"):
        monkeypatch.delenv(var, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert hub_url() == want
    assert _hub_base_url() == want


def test_default_endpoint_still_means_adbs_own_port() -> None:
    """"Nothing set" still lands on adb's 5037, not on our forwarder; the
    description must say so."""
    from devicehands.infra.adb_endpoint import AdbEndpoint
    from devicehands.transport.adb_socket import DEFAULT_PORT

    assert DEFAULT_PORT == ports.ADB_SERVER_DEFAULT_PORT == 5037
    assert "5037" in AdbEndpoint(None, "default").described


def test_bench_program_status_port_matches() -> None:
    """The bench program is a standalone script (no devicehands import), so it
    keeps its own copy; read it with ast and compare."""
    tree = ast.parse(_BENCH.read_text(encoding="utf-8"))
    values = [
        n.value.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "DEFAULT_STATUS_PORT" for t in n.targets)
        and isinstance(n.value, ast.Constant)
    ]
    assert values == [ports.DEFAULT_BENCH_STATUS_PORT]
