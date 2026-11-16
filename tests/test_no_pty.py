"""The entry modules import on a system without fcntl / termios / pty.

Windows has none of the three. They used to be imported at module level by
devicehands.transport.interactive, which dhands-hub reaches through the web
terminal route, so `dhands-hub` could not even start there. These tests
take the modules away in a fresh interpreter (``sys.modules[name] = None``
makes ``import name`` raise ImportError) and import every entry point.

pyserial is imported before the modules are taken away. On Linux it loads
its POSIX backend, which needs fcntl / termios; on Windows it loads its
win32 backend instead, so pre-loading it here stands in for that, and the
check stays about our own code.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_POSIX_ONLY = ("fcntl", "termios", "pty", "tty")

_PRELUDE = textwrap.dedent(
    f"""
    import sys
    import serial, serial_asyncio  # see module docstring
    for _name in {_POSIX_ONLY!r}:
        sys.modules[_name] = None
    """
)


def _entry_modules() -> list[str]:
    scripts = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
    return sorted({target.split(":", 1)[0] for target in scripts.values()})


def _run(body: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", _PRELUDE + textwrap.dedent(body)],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=_ROOT,
    )


def test_entry_modules_are_read_from_pyproject() -> None:
    """Guard the guard: an empty list would pass the test below."""
    assert {"devicehands.cli.main", "devicehands.mcp.server", "devicehands.api.server"} <= set(
        _entry_modules()
    )


def test_the_simulation_really_removes_the_modules() -> None:
    """Reverse check: with the prelude in place, importing the modules must
    fail, or the import test below proves nothing."""
    r = _run(
        """
        for name in ("fcntl", "termios", "pty"):
            try:
                __import__(name)
            except ImportError:
                pass
            else:
                raise SystemExit(f"{name} still importable")
        print("removed")
        """
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "removed"


def test_entry_modules_import_without_posix_modules() -> None:
    modules = _entry_modules()
    r = _run(
        f"""
        import importlib
        for m in {modules!r}:
            importlib.import_module(m)
        from devicehands.transport.interactive import pty_supported
        print("pty_supported", pty_supported())
        """
    )
    assert r.returncode == 0, r.stderr[-2000:]
    assert r.stdout.strip().splitlines()[-1] == "pty_supported False"


def test_web_terminal_reports_a_structured_error_without_a_pty(tmp_path: Path) -> None:
    r = _run(
        f"""
        import json, os
        os.environ["DHANDS_WORKSPACE"] = {str(tmp_path)!r}
        from fastapi.testclient import TestClient
        from devicehands.api.server import create_app

        with TestClient(create_app()) as client:
            with client.websocket_connect("/terminal/ws") as ws:
                msg = ws.receive_json()
        print(json.dumps(msg))
        """
    )
    assert r.returncode == 0, r.stderr[-2000:]
    msg = json.loads(r.stdout.strip().splitlines()[-1])
    assert msg["type"] == "closed"
    assert msg["exit_code"] == -1
    assert msg["error"]["code"] == "TERMINAL_UNSUPPORTED_OS"
    assert "not supported on this operating system" in msg["error"]["message"]


def test_pty_supported_on_this_system() -> None:
    """The normal case: Linux / macOS keep the web terminal."""
    from devicehands.transport.interactive import pty_supported

    assert pty_supported() is (sys.platform != "win32")
