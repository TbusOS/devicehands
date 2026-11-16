"""`dhands-hub` command-line handling.

The defect these guard: before argument parsing existed, `dhands-hub --help`
ignored the flag and started the server, binding real ports on a machine
where another service was already running. Every case here replaces the
function that starts the server, so no test binds a port.
"""

from __future__ import annotations

import pytest

from devicehands import __version__
from devicehands.api import server


def _must_not_start(*args, **kwargs):  # pragma: no cover - only runs on a regression
    pytest.fail(f"dhands-hub started the server: args={args} kwargs={kwargs}")


@pytest.fixture
def no_start(monkeypatch):
    monkeypatch.setattr(server, "_serve", _must_not_start)


def test_help_prints_usage_and_exits_zero(no_start, capsys):
    with pytest.raises(SystemExit) as exc:
        server.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "usage: dhands-hub" in out
    # The help says what running it does and which variables it reads.
    assert "starts the HTTP" in out
    for name in ("DHANDS_API_HOST", "DHANDS_API_PORT", "DHANDS_AGENT_TOKEN", "DHANDS_WORKSPACE"):
        assert name in out
    assert str(server.DEFAULT_API_PORT) in out


def test_version_prints_and_exits_zero(no_start, capsys):
    with pytest.raises(SystemExit) as exc:
        server.main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"dhands-hub {__version__}"


@pytest.mark.parametrize("argv", [["--port", "9000"], ["serve"], ["-x"]])
def test_unknown_argument_exits_two_without_starting(no_start, capsys, argv):
    with pytest.raises(SystemExit) as exc:
        server.main(argv)
    assert exc.value.code == 2
    assert "usage: dhands-hub" in capsys.readouterr().err


def test_main_reads_sys_argv_when_called_without_arguments(no_start, monkeypatch):
    """The console script calls main() with no arguments; the flags must still
    be seen through sys.argv."""
    monkeypatch.setattr("sys.argv", ["dhands-hub", "--version"])
    with pytest.raises(SystemExit) as exc:
        server.main()
    assert exc.value.code == 0


def test_no_arguments_starts_the_server(monkeypatch):
    """Reverse check: with no arguments main() does reach the start function,
    with the host / port / deflate taken from the environment."""
    calls = []
    monkeypatch.setattr(server, "_serve", lambda host, port, deflate: calls.append((host, port, deflate)))
    monkeypatch.setenv("DHANDS_API_HOST", "127.0.0.1")
    monkeypatch.delenv("DHANDS_API_PORT", raising=False)
    monkeypatch.delenv("DHANDS_WS_DEFLATE", raising=False)
    monkeypatch.setattr(server.app.state, "warm_model", False, raising=False)
    server.main([])
    assert calls == [("127.0.0.1", server.DEFAULT_API_PORT, False)]


def test_no_arguments_honours_port_and_deflate_env(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "_serve", lambda host, port, deflate: calls.append((host, port, deflate)))
    monkeypatch.setenv("DHANDS_API_HOST", "127.0.0.1")
    monkeypatch.setenv("DHANDS_API_PORT", "12345")
    monkeypatch.setenv("DHANDS_WS_DEFLATE", "1")
    monkeypatch.setattr(server.app.state, "warm_model", False, raising=False)
    server.main([])
    assert calls == [("127.0.0.1", 12345, True)]
