"""Web API: dial-home rendezvous for the bench program (protocol v2).

    /bench/connect  — the bench's persistent SIGNALING WebSocket (control
                      frames only, protocol/v2/schema/control.json).
    /bench/channel  — a per-channel DATA WebSocket the bench dials back, one
                      per open_channel. Correlated by the ?cid= query param
                      and authenticated by the per-channel secret (DEBT-084).

The rules this route follows are protocol/v2/sequences.md: the token is a
header and is checked before anything else; the first frame must be a hello,
and a bad one, an unknown verb or a version range that misses ours is
answered with an `error` frame and close code 1002; after the handshake a
bad or unknown frame is answered with an `error` frame and the session goes
on. Dial-back credentials ride headers only (ADR-055) — the cid, a routing
key and not a secret, is the only thing in the URL.

Security (ADR-050 §6): both endpoints validate the bench token (env
DHANDS_AGENT_TOKEN; when unset the API is open, same posture as the rest of
dhands-hub which prints a no-auth banner). The token check happens BEFORE any
registry insert / forwarder attach. tcp channel targets are allowlisted to the
bench host's adb server on both sides.

The adb forwarder binds 127.0.0.1:8776 by default (infra.ports — deliberately
not adb's own 5037); DHANDS_ADB_FORWARD_PORT can override it (tests use 0 for
an ephemeral port).
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from devicehands.api.schema import API_VERSION
from devicehands.remote import protocol
from devicehands.remote.bench_board import parse_agent_boards
from devicehands.remote.bench_ops import parse_ops
from devicehands.remote.forwarder import (
    forwarder_status,
    get_adb_forwarder,
    get_serial_forwarder,
    serial_configured,
)
from devicehands.remote.protocol import FrameError, Verb
from devicehands.remote.registry import AgentConnection, get_agent_registry

_log = logging.getLogger(__name__)
router = APIRouter()

HELLO_TIMEOUT_S = protocol.HELLO_TIMEOUT_S
HEARTBEAT_TIMEOUT_S = protocol.HEARTBEAT_TIMEOUT_S
# What the hub accepts once the session is up; `hello` is the first frame only.
_SESSION_VERBS = protocol.HUB_RECEIVES - {Verb.HELLO}


def _expected_token() -> str | None:
    return os.environ.get("DHANDS_AGENT_TOKEN")


def _token_ok(supplied: str | None) -> bool:
    expected = _expected_token()
    if not expected:
        return True  # no token configured = open (dev posture)
    if not supplied:
        return False
    return hmac.compare_digest(supplied.encode("utf-8", "replace"), expected.encode("utf-8"))


class _WsDataChannel:
    """DataChannel backed by a dialed-back data WebSocket (raw bytes).

    A text message or an empty binary message on a data connection is the
    end of that connection (protocol/v2/framing.md section 5)."""

    def __init__(self, ws: WebSocket) -> None:
        self._ws = ws
        self._closed = asyncio.Event()

    async def recv(self) -> bytes:
        try:
            msg = await self._ws.receive()
        except (WebSocketDisconnect, RuntimeError):
            return b""
        if msg.get("type") == "websocket.disconnect":
            return b""
        data = msg.get("bytes")
        if isinstance(data, bytes):
            return data
        return b""

    async def send(self, data: bytes) -> None:
        await self._ws.send_bytes(data)

    async def aclose(self) -> None:
        self._closed.set()

    async def wait_closed(self) -> None:
        await self._closed.wait()


class _Gone(Exception):
    """The signaling connection closed or went silent."""


async def _receive(ws: WebSocket, timeout: float) -> str | bytes:  # noqa: ASYNC109
    """One signaling message, text or binary. Raises _Gone on disconnect or
    when nothing arrives within `timeout`."""
    try:
        msg = await asyncio.wait_for(ws.receive(), timeout=timeout)
    except (TimeoutError, WebSocketDisconnect, RuntimeError) as e:
        raise _Gone from e
    if not isinstance(msg, dict) or msg.get("type") == "websocket.disconnect":
        raise _Gone
    text = msg.get("text")
    if isinstance(text, str):
        return text
    data = msg.get("bytes")
    return data if isinstance(data, bytes) else b""


def _is_error_frame(raw: str | bytes) -> bool:
    """An `error` frame is never answered with another, even a malformed one
    (framing.md section 2) — two sides that disagree would trade errors forever."""
    if not isinstance(raw, str):
        return False
    try:
        obj = json.loads(raw)
    except ValueError:
        return False
    return isinstance(obj, dict) and obj.get("verb") == Verb.ERROR.value


async def _refuse(ws: WebSocket, frame: dict[str, Any]) -> None:
    """Answer a handshake that cannot go on, then close with 1002."""
    with contextlib.suppress(Exception):
        await ws.send_text(protocol.encode_control(frame))
    with contextlib.suppress(Exception):
        await ws.close(code=1002)


async def _safe_attach(get_forwarder: Any, label: str) -> None:
    """Attach a forwarder without letting a bind failure tear down the bench
    session. A taken port — another process on the forwarder's port — otherwise
    propagates out of bench_connect, drops the session before hello_ok, and
    traps the bench in an endless reconnect loop. Instead we log a loud,
    actionable warning and carry on: signaling + the other channel keep
    working, and /agent/status shows the forwarder unbound."""
    try:
        await get_forwarder().attach()
    except OSError as e:
        _log.warning(
            "%s forwarder could not bind (%s) — that device path stays "
            "unavailable until the conflict clears (another program on the "
            "port, e.g. a second hub? stop it, or set DHANDS_ADB_FORWARD_PORT / "
            "DHANDS_SERIAL_FORWARD_PORT to a free port). Bench stays connected.",
            label,
            e,
        )


async def _handshake(ws: WebSocket) -> tuple[dict[str, Any], int] | None:
    """Read and check the hello. Returns (hello, negotiated version), or None
    after answering and closing (sequences.md section 2)."""
    try:
        raw = await _receive(ws, HELLO_TIMEOUT_S)
    except _Gone:
        with contextlib.suppress(Exception):
            await ws.close(code=1002)
        return None
    try:
        hello = protocol.decode_control(raw, accept=frozenset({Verb.HELLO}))
    except FrameError as e:
        await _refuse(ws, protocol.error(e.code, e.message, re_id=e.re_id))
        return None
    lo, hi = int(hello["proto"]["min"]), int(hello["proto"]["max"])
    chosen = protocol.negotiate(lo, hi)
    if chosen is None:
        _log.warning("bench %r refused: protocol %s..%s", hello.get("bench_id"), lo, hi)
        await _refuse(ws, protocol.version_refusal(lo, hi))
        return None
    return hello, chosen


@router.websocket(protocol.CONNECT_PATH)
async def bench_connect(ws: WebSocket) -> None:
    await ws.accept()
    if not _token_ok(ws.headers.get(protocol.TOKEN_HEADER)):
        with contextlib.suppress(Exception):
            await ws.close(code=1008)  # policy violation, before any frame
        return
    registry = get_agent_registry()
    conn: AgentConnection | None = None
    epoch = 0

    try:
        shaken = await _handshake(ws)
        if shaken is None:
            return
        hello, chosen = shaken

        async def send_control(m: dict[str, Any]) -> None:
            await ws.send_text(protocol.encode_control(m))

        bench_id = str(hello["bench_id"]).strip()
        conn = AgentConnection(
            agent_id=bench_id,
            name=str(hello["name"] or bench_id),
            proto=chosen,
            impl_lang=str(hello["impl"]["lang"]),
            os_name=str(hello["os"]),
            build=str(hello["impl"]["build"]),
            ops=parse_ops(hello["ops"]) or {},
            # Per-board products (ADR-063 phase 2).
            boards=parse_agent_boards(hello["boards"]),
            # What the bench's last remote update did (ADR-060); optional.
            last_update=hello.get("update"),
            send_control=send_control,
            registry=registry,
        )
        epoch = await registry.register(conn)
        # The forwarders are PROCESS-LEVEL singletons (ADR-051): attach() binds
        # the OS listener once and is idempotent, so a reconnecting / second
        # bench never re-binds the port (no EADDRINUSE race). They route to the
        # current bench via the registry, so we do NOT detach on disconnect. The
        # serial forwarder only binds when a COM is configured on this hub.
        await _safe_attach(get_adb_forwarder, "adb")
        if serial_configured():
            await _safe_attach(get_serial_forwarder, "serial")
        await send_control(protocol.hello_ok(proto=chosen))
        # populate the device cache so the Connection Center has data right away
        await conn.request_device_list()
        await _session(ws, conn, send_control)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        _log.warning("bench_connect error: %s", e)
    finally:
        # teardown on ALL paths (checklist #4): compare-and-clear unregister,
        # and fail the opens still waiting on this connection — nothing can
        # answer them any more. The forwarder stays bound (process-level); it
        # just sees no current bench until one reconnects.
        if conn is not None:
            registry.fail_owned(conn, "the bench disconnected before the channel opened")
            with contextlib.suppress(Exception):
                await registry.unregister(conn.agent_id, epoch)
        with contextlib.suppress(Exception):
            await ws.close()


async def _session(ws: WebSocket, conn: AgentConnection, send_control: Any) -> None:
    """The recv loop after hello_ok: heartbeats, replies, channel errors.
    Ends on disconnect or HEARTBEAT_TIMEOUT_S of silence."""
    registry = get_agent_registry()
    while True:
        try:
            raw = await _receive(ws, HEARTBEAT_TIMEOUT_S)
        except _Gone:
            return
        try:
            frame = protocol.decode_control(raw, accept=_SESSION_VERBS)
        except FrameError as e:
            _log.warning("bench %s sent a frame the hub cannot use: %s", conn.agent_id, e)
            if not _is_error_frame(raw):
                await send_control(protocol.error(e.code, e.message, re_id=e.re_id))
            continue
        verb = frame["verb"]
        if verb == Verb.HEARTBEAT.value:
            continue
        if verb == Verb.CHANNEL_ERROR.value:
            # The bench could not open the channel we asked for (COM held by
            # another program, adb server gone, ...). Wake the waiter NOW with
            # the bench's own reason instead of making it wait out the
            # dial-back timeout — issue #4. Only for a cid this connection was
            # asked to open (sequences.md §5 rule 4).
            if registry.fail_pending(frame["cid"], frame["reason"], code=frame["code"], owner=conn):
                _log.warning(
                    "bench %s could not open channel: %s %s",
                    conn.agent_id,
                    frame["code"],
                    frame["reason"],
                )
            continue
        if verb == Verb.ERROR.value:
            _log.warning(
                "bench %s answered a hub frame with %s: %s",
                conn.agent_id,
                frame["code"],
                frame["message"],
            )
            continue
        conn.apply_reply(frame)  # adb_list / com_list


@router.websocket(protocol.CHANNEL_PATH)
async def bench_channel(ws: WebSocket) -> None:
    await ws.accept()
    cid = ws.query_params.get("cid")
    token = ws.headers.get(protocol.TOKEN_HEADER)
    csecret = ws.headers.get(protocol.CSECRET_HEADER)
    if not cid or not _token_ok(token):
        with contextlib.suppress(Exception):
            await ws.close(code=1008)
        return

    registry = get_agent_registry()
    channel = _WsDataChannel(ws)
    # The endpoint token gates WHO may dial back; the per-channel secret
    # (DEBT-084) gates WHICH channel this dial-back may claim — only the bench
    # that received the open_channel knows it.
    if not registry.resolve_pending(cid, channel, csecret):
        # unknown / expired cid or wrong secret — do not leave a dangling WS
        with contextlib.suppress(Exception):
            await ws.close(code=1008)
        return

    # The forwarder now owns `channel` and shuttles bytes. Keep this coroutine
    # alive until the forwarder closes the channel (aclose sets the event);
    # only this handler reads the WS via channel.recv(), so no double-read race.
    try:
        await channel.wait_closed()
    finally:
        with contextlib.suppress(Exception):
            await ws.close()


@router.post("/api/agent/adb/restart")
async def agent_adb_restart(kill_conflicts: bool = False) -> dict[str, Any]:
    """Ask the current agent to restart its LOCAL adb server and re-report
    devices. Fire-and-forget by design (same posture as the device refresh):
    the adb_list reply lands on the signaling WS and updates the cache, so the
    caller polls /agent/status to see the outcome. 409 when no agent is
    connected — nothing could receive the request.

    ?kill_conflicts=true additionally asks the agent to first terminate
    adb-flavoured foreign processes (renamed vendor adb builds hold the
    exclusive USB interface; /agent/status lists them under adb_conflicts).
    Boolean on purpose — the agent's own heuristic decides what matches, the
    hub can never name a process."""
    registry = get_agent_registry()
    current = registry.current_agent()
    if current is None:
        raise HTTPException(status_code=409, detail="no agent connected")
    await current.request_adb_restart(kill_conflicts=kill_conflicts)
    return {
        "v": API_VERSION,
        "ok": True,
        "agent_id": current.agent_id,
        "kill_conflicts": kill_conflicts,
        "note": "restart requested — poll /agent/status for the refreshed device list",
    }


@router.get("/agent/status")
async def agent_status() -> dict[str, Any]:
    """Snapshot for the web Connection Center: connected remote agents (with
    their last-reported devices) + adb/serial forwarder state. Fires a
    fire-and-forget device refresh so the next poll reflects plug/unplug."""
    registry = get_agent_registry()
    await registry.request_device_refresh()
    current = registry.current_agent()
    current_id = current.agent_id if current else None
    agents = [{**a, "current": a["agent_id"] == current_id} for a in registry.list_agents()]
    return {
        "v": API_VERSION,
        "agents": agents,
        "forwarders": forwarder_status(),
    }
