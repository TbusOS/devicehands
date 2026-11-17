"""Conformance harness: a bench program under test, and the smallest hub
that can talk to it.

The bench runs as a separate process and is reached only through the
protocol, so the same tests check the Python bench today and the Rust one
later. What the harness relies on is the bench's command line and config
file, not its code:

    <bench command> --config <dir>/bench.conf

The command is `DHANDS_BENCH_CMD` (a JSON list, e.g.
`["/path/to/dhands-bench"]`); unset, it is the Python bench of this
repository, copied into a temporary directory first because it keeps its
state next to its own file. The config keys the harness writes:

    hub_url  token  agent_id  name  status_port=0  log_file=none
    adb_path  adb_port  heartbeat_s  fastboot_path

Every frame the hub double receives is checked against protocol/v2/schema;
a frame that fails the schema fails the test that received it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from tests.protocol import schema_v2 as S

REPO = Path(__file__).resolve().parents[2]
PY_BENCH = REPO / "bench-py" / "dhands_bench.py"
TOKEN = "conformance-token-0001"
BENCH_ID = "conformance-bench"
CONNECT_PATH = "/bench/connect"
CHANNEL_PATH = "/bench/channel"
TOKEN_HEADER = "x-dhands-token"
CSECRET_HEADER = "x-dhands-csecret"
# How long a bench may take to start and dial in: a cold interpreter on a
# shared CI runner is slow, Windows most of all.
CONNECT_TIMEOUT_S = 30.0
FRAME_TIMEOUT_S = 10.0


def check_control(frame: Any, *, direction: str = "bench->hub") -> None:
    """A frame the bench sent must be a v2 control frame of its direction."""
    S.assert_valid("control.json", frame)
    schema = S.SCHEMAS[S.control_schema_of(frame)]
    assert schema.get("x-direction") in (direction, "both"), (
        f"the bench sent {frame.get('verb')!r}, which only travels {schema.get('x-direction')}"
    )


# ── the hub double ───────────────────────────────────────────────────


@dataclass
class Session:
    """One signaling connection from the bench."""

    ws: ServerConnection
    headers: dict[str, str]
    received: list[Any] = field(default_factory=list)

    async def recv_raw(self, wait_s: float = FRAME_TIMEOUT_S) -> str | bytes:
        return await asyncio.wait_for(self.ws.recv(), wait_s)

    async def recv(self, wait_s: float = FRAME_TIMEOUT_S, *, heartbeats: bool = False) -> Any:
        """The next control frame, schema-checked. Heartbeats are skipped
        unless asked for."""
        deadline = time.monotonic() + wait_s
        while True:
            left = max(0.0, deadline - time.monotonic())
            raw = await self.recv_raw(left)
            assert isinstance(raw, str), f"the bench sent a binary message on signaling: {raw!r}"
            frame = json.loads(raw)
            self.received.append(frame)
            check_control(frame)
            if frame.get("verb") == "heartbeat" and not heartbeats:
                continue
            return frame

    async def expect(self, verb: str, wait_s: float = FRAME_TIMEOUT_S) -> dict[str, Any]:
        frame = await self.recv(wait_s)
        assert frame.get("verb") == verb, f"expected {verb}, got {frame!r}"
        assert isinstance(frame, dict)
        return frame

    async def send(self, msg: Any) -> None:
        if isinstance(msg, bytes | str):
            await self.ws.send(msg)
        else:
            await self.ws.send(json.dumps(msg))

    async def handshake(self, proto: int = 2) -> dict[str, Any]:
        """Read hello, answer hello_ok. Returns the hello."""
        hello = await self.expect("hello")
        await self.send({"verb": "hello_ok", "proto": proto})
        return hello

    async def silent(self, seconds: float) -> list[Any]:
        """Frames (other than heartbeats) that arrive within `seconds`."""
        got: list[Any] = []
        deadline = time.monotonic() + seconds
        while (left := deadline - time.monotonic()) > 0:
            try:
                got.append(await self.recv(left))
            except TimeoutError:
                break
        return got

    async def closed(self, wait_s: float) -> bool:
        try:
            await asyncio.wait_for(self.ws.wait_closed(), wait_s)
        except TimeoutError:
            return False
        return True


@dataclass
class DialBack:
    """One data connection the bench dialed back."""

    ws: ServerConnection
    cid: str
    headers: dict[str, str]
    done: asyncio.Event = field(default_factory=asyncio.Event)


class HubDouble:
    """The smallest hub: accepts /bench/connect and /bench/channel and hands
    each connection to the test. It checks nothing on its own beyond the
    token header; the tests decide what to answer."""

    def __init__(self, *, token: str = TOKEN, reject_token: bool = False) -> None:
        self.token = token
        self.reject_token = reject_token
        self.reject_dialback = False
        self.connects = 0
        self.sessions: asyncio.Queue[Session] = asyncio.Queue()
        self._dialbacks: dict[str, asyncio.Future[DialBack]] = {}
        self._server: Server | None = None
        self.port = 0

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}{CONNECT_PATH}"

    async def start(self) -> None:
        self._server = await serve(self._handle, "127.0.0.1", 0, max_size=None)
        self.port = next(iter(self._server.sockets)).getsockname()[1]

    async def stop(self) -> None:
        for fut in self._dialbacks.values():
            if fut.done() and not fut.cancelled():
                fut.result().done.set()
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._server.wait_closed(), 10)

    def _future(self, cid: str) -> asyncio.Future[DialBack]:
        if cid not in self._dialbacks:
            self._dialbacks[cid] = asyncio.get_running_loop().create_future()
        return self._dialbacks[cid]

    async def dialback(self, cid: str, wait_s: float = FRAME_TIMEOUT_S) -> DialBack:
        return await asyncio.wait_for(asyncio.shield(self._future(cid)), wait_s)

    async def _handle(self, ws: ServerConnection) -> None:
        req = ws.request
        assert req is not None
        url = urlsplit(req.path)
        headers = {k.lower(): v for k, v in req.headers.raw_items()}
        if url.path == CONNECT_PATH:
            self.connects += 1
            if self.reject_token or headers.get(TOKEN_HEADER) != self.token:
                await ws.close(1008, "bad token")
                return
            await self.sessions.put(Session(ws, headers))
            await ws.wait_closed()
        elif url.path == CHANNEL_PATH:
            cid = (parse_qs(url.query).get("cid") or [""])[0]
            if self.reject_dialback:
                await ws.close(1008, "unknown channel")
                return
            dial = DialBack(ws, cid, headers)
            fut = self._future(cid)
            if not fut.done():
                fut.set_result(dial)
            await dial.done.wait()
            with contextlib.suppress(Exception):
                await ws.close()
        else:
            await ws.close(1008, "unknown path")

    async def session(self, wait_s: float = CONNECT_TIMEOUT_S) -> Session:
        return await asyncio.wait_for(self.sessions.get(), wait_s)


# ── fake tools ───────────────────────────────────────────────────────

_FAKE_TOOL = r"""
import sys
args = sys.argv[1:]
if args[:1] == ["-P"]:
    args = args[2:]
if "adb" in TOOL:
    if args == ["devices"]:
        sys.stdout.write("List of devices attached\nEXAMPLE0001\tdevice\n")
        sys.exit(0)
    if args in (["kill-server"], ["start-server"]):
        sys.exit(0)
else:
    if args == ["devices"]:
        sys.stdout.write("EXAMPLE0001\tfastboot\n")
        sys.exit(0)
    if len(args) == 2 and args[0] == "getvar":
        sys.stderr.write(args[1] + ": EXAMPLE-VALUE\nFinished. Total time: 0.001s\n")
        sys.exit(0)
sys.stderr.write("fake " + TOOL + ": unsupported " + " ".join(args) + "\n")
sys.exit(1)
"""


def fake_tool(directory: Path, name: str) -> Path:
    """An executable `name` that answers the few commands the tests send.
    A Python script behind a wrapper: `.cmd` on Windows (a script with a
    shebang does not start there), a shell script elsewhere."""
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / f"{name}_fake.py"
    script.write_bytes(f"TOOL = {name!r}\n{_FAKE_TOOL}".encode())
    if sys.platform == "win32":
        exe = directory / f"{name}.cmd"
        exe.write_bytes(f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n'.encode())
    else:
        exe = directory / name
        exe.write_bytes(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n'.encode())
        exe.chmod(0o755)
    return exe


# ── the bench process ────────────────────────────────────────────────


def bench_command(workdir: Path) -> list[str]:
    raw = os.environ.get("DHANDS_BENCH_CMD", "")
    if raw:
        cmd = json.loads(raw)
        assert isinstance(cmd, list) and cmd and all(isinstance(a, str) for a in cmd), (
            "DHANDS_BENCH_CMD must be a JSON list of strings"
        )
        return cmd
    copy = workdir / "bench" / PY_BENCH.name
    copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(PY_BENCH, copy)
    return [sys.executable, str(copy)]


@dataclass
class Bench:
    proc: subprocess.Popen[bytes]
    log: Path

    def output(self) -> str:
        return self.log.read_bytes().decode("utf-8", "replace")[-4000:]

    def running(self) -> bool:
        return self.proc.poll() is None

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(10)


def start_bench(workdir: Path, hub_url: str, **config: Any) -> Bench:
    """Write bench.conf and start the bench under test."""
    conf: dict[str, Any] = {
        "hub_url": hub_url,
        "token": TOKEN,
        "agent_id": BENCH_ID,
        "name": "conformance bench",
        "status_port": 0,
        "log_file": "none",
        "heartbeat_s": 600,
        # Never the host's real adb: a fake executable and a port nothing
        # listens on, unless a test passes its own.
        "adb_path": fake_tool(workdir / "tools", "adb"),
        "adb_port": closed_port(),
    }
    conf.update(config)
    path = workdir / "bench.conf"
    path.write_bytes(
        "".join(f"{k}={v}\n" for k, v in conf.items() if v is not None).encode("utf-8")
    )
    log = workdir / "bench-output.txt"
    env = {k: v for k, v in os.environ.items() if k != "DHANDS_BENCH_LAUNCHER"}
    env.update(PYTHONUNBUFFERED="1", NO_COLOR="1")
    with open(log, "wb") as out:
        proc = subprocess.Popen(
            [*bench_command(workdir), "--config", str(path)],
            cwd=workdir,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            env=env,
        )
    return Bench(proc, log)


async def wait_exit(bench: Bench, wait_s: float) -> bool:
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if not bench.running():
            return True
        await asyncio.sleep(0.1)
    return False


async def recv_bytes(ws: ServerConnection, n: int, wait_s: float = FRAME_TIMEOUT_S) -> bytes:
    """Read binary messages until `n` bytes arrived."""
    got = b""
    deadline = time.monotonic() + wait_s
    while len(got) < n:
        msg = await asyncio.wait_for(ws.recv(), max(0.0, deadline - time.monotonic()))
        assert isinstance(msg, bytes), f"text message on a data connection: {msg!r}"
        got += msg
    return got


# ── job channel ──────────────────────────────────────────────────────

_JOB_HEADER = struct.Struct(">cI")


def job_control(msg: dict[str, Any]) -> bytes:
    payload = json.dumps(msg).encode("utf-8")
    return _JOB_HEADER.pack(b"C", len(payload)) + payload


class JobReader:
    """Reassembles job frames from a data connection, schema-checking every
    event the bench sends."""

    def __init__(self, ws: ServerConnection) -> None:
        self._ws = ws
        self._buf = b""

    async def _fill(self, n: int, deadline: float) -> bool:
        while len(self._buf) < n:
            try:
                msg = await asyncio.wait_for(self._ws.recv(), max(0.0, deadline - time.monotonic()))
            except ConnectionClosed:
                return False
            assert isinstance(msg, bytes), f"text message on a job channel: {msg!r}"
            assert msg, "an empty binary message on a job channel"
            self._buf += msg
        return True

    async def event(self, wait_s: float = FRAME_TIMEOUT_S) -> dict[str, Any] | None:
        """The next event, or None when the bench closed the channel."""
        deadline = time.monotonic() + wait_s
        if not await self._fill(5, deadline):
            assert not self._buf, "the channel closed inside a frame header"
            return None
        kind, length = _JOB_HEADER.unpack(self._buf[:5])
        assert kind == b"C", "the bench sends only control frames on a job channel"
        assert await self._fill(5 + length, deadline), "the channel closed inside a frame"
        msg = json.loads(self._buf[5 : 5 + length])
        self._buf = self._buf[5 + length :]
        S.assert_valid("job-event.json", msg)
        assert isinstance(msg, dict)
        return msg

    async def until_done(self, wait_s: float = FRAME_TIMEOUT_S) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        while True:
            ev = await self.event(wait_s)
            assert ev is not None, f"job channel closed without done after {events!r}"
            events.append(ev)
            if ev["ev"] == "done":
                return events


def closed_port() -> int:
    """A local port nothing listens on (bound, then released)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
