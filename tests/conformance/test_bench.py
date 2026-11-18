"""Conformance: the bench program, as a separate process, against protocol v2.

Nothing here imports the bench. It is started with a config file and
reached only through /bench/connect and /bench/channel (harness.py says how
to point these tests at another implementation). Each test names the rule
it checks in protocol/v2/sequences.md (§) or framing.md (F§).
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from devicehands.remote import rule_pattern
from tests.conformance.harness import (
    BENCH_ID,
    CSECRET_HEADER,
    TOKEN,
    TOKEN_HEADER,
    Bench,
    HubDouble,
    JobReader,
    Session,
    closed_port,
    fake_tool,
    job_control,
    recv_bytes,
    wait_exit,
)
from tests.protocol import schema_v2 as S

Launch = Callable[..., Bench]

# A bench that reconnects after a refusal does so within its first backoff
# step (1 s); waiting a few of those shows it does not.
NO_RECONNECT_WAIT_S = 3.5
FRAME_WAIT = 10.0


async def _connected(hub: HubDouble, launch: Launch, **config: Any) -> tuple[Bench, Session]:
    bench = launch(**config)
    session = await hub.session()
    await session.handshake()
    return bench, session


def _cid() -> str:
    return secrets.token_hex(16)


async def _open(session: Session, channel_type: str, params: dict[str, Any]) -> tuple[str, str]:
    cid, csecret = _cid(), secrets.token_urlsafe(32)
    await session.send(
        {
            "verb": "open_channel",
            "cid": cid,
            "csecret": csecret,
            "channel_type": channel_type,
            "params": params,
        }
    )
    return cid, csecret


async def _channel_error(session: Session, cid: str) -> dict[str, Any]:
    frame = await session.expect("channel_error")
    assert frame["cid"] == cid
    return frame


# ── §2 handshake ─────────────────────────────────────────────────────


async def test_handshake_hello_is_v2_and_the_token_rides_the_header(
    hub: HubDouble, bench: Launch
) -> None:
    """§1, §2: the hello is a v2 hello, the credential is a header and never
    in a frame, and after hello_ok the session answers requests."""
    bench()
    session = await hub.session()
    hello = await session.expect("hello")
    S.assert_valid("hello.json", hello)
    assert session.headers.get(TOKEN_HEADER) == TOKEN
    assert hello["proto"]["min"] <= 2 <= hello["proto"]["max"]
    assert hello["bench_id"] == BENCH_ID
    assert TOKEN not in str(hello)
    for op in hello["ops"]:
        for param in op["params"]:
            if "pattern" in param:
                assert rule_pattern.problem(param["pattern"]) == "", (op["name"], param)
    await session.send({"verb": "hello_ok", "proto": 2})
    await session.send({"verb": "list_com", "id": 41})
    reply = await session.expect("com_list")
    assert reply["re"] == 41


async def test_fastboot_rows_follow_the_fastboot_path(
    hub: HubDouble, bench: Launch, tmp_path: Path
) -> None:
    """DESIGN.md 3.7: no capability list; a configured fastboot shows as rows
    of group fastboot, with the partition rule on the wire (3.6)."""
    fastboot = fake_tool(tmp_path / "tools", "fastboot")
    bench(fastboot_path=fastboot)
    session = await hub.session()
    hello = await session.expect("hello")
    ops = {op["name"]: op for op in hello["ops"]}
    assert {"devices", "getvar", "flash"} <= set(ops)
    assert all(ops[n]["group"] == "fastboot" for n in ("devices", "getvar", "flash"))
    (partition,) = ops["flash"]["params"]
    assert partition["kind"] == "partition" and partition["pattern"] and partition["max_len"]


async def test_version_refusal_stops_the_bench_from_reconnecting(
    hub: HubDouble, bench: Launch
) -> None:
    """§2: error PROTO_VERSION_UNSUPPORTED then close 1002 -> no reconnect."""
    bench()
    session = await hub.session()
    await session.expect("hello")
    await session.send(
        {
            "verb": "error",
            "code": "PROTO_VERSION_UNSUPPORTED",
            "message": "this hub speaks 3..3",
            "supported": {"min": 3, "max": 3},
        }
    )
    await session.ws.close(1002)
    await asyncio.sleep(NO_RECONNECT_WAIT_S)
    assert hub.connects == 1


async def test_hello_ok_outside_the_bench_range_is_refused(hub: HubDouble, bench: Launch) -> None:
    """§2: a proto the bench did not offer -> it closes and does not reconnect."""
    bench()
    session = await hub.session()
    await session.expect("hello")
    await session.send({"verb": "hello_ok", "proto": 99})
    assert await session.closed(FRAME_WAIT), "the bench kept a session on proto 99"
    await asyncio.sleep(NO_RECONNECT_WAIT_S)
    assert hub.connects == 1


async def test_wrong_token_stops_the_bench_from_reconnecting(hub: HubDouble, bench: Launch) -> None:
    """§1, §2: 1008 on connect -> no reconnect."""
    hub.reject_token = True
    b = bench()
    for _ in range(300):
        if hub.connects:
            break
        await asyncio.sleep(0.1)
    assert hub.connects == 1, b.output()
    await asyncio.sleep(NO_RECONNECT_WAIT_S)
    assert hub.connects == 1


async def test_a_dropped_session_is_reconnected(hub: HubDouble, bench: Launch) -> None:
    """§2: a session that ends for another reason is dialled again."""
    _, session = await _connected(hub, bench)
    await session.ws.close(1001)
    again = await hub.session(wait_s=15)
    hello = await again.expect("hello")
    assert hello["bench_id"] == BENCH_ID


# ── §3 heartbeat ─────────────────────────────────────────────────────


async def test_heartbeats_arrive_at_the_configured_interval(hub: HubDouble, bench: Launch) -> None:
    _, session = await _connected(hub, bench, heartbeat_s=0.3)
    beats = 0
    for _ in range(3):
        frame = await session.recv(wait_s=5, heartbeats=True)
        beats += frame["verb"] == "heartbeat"
    assert beats == 3


# ── F§2 unknown verbs and bad frames ─────────────────────────────────


async def test_unknown_verb_is_answered_and_the_session_continues(
    hub: HubDouble, bench: Launch
) -> None:
    _, session = await _connected(hub, bench)
    await session.send({"verb": "list_usb", "id": 7, "future_field": True})
    err = await session.expect("error")
    assert (err["code"], err.get("re")) == ("UNKNOWN_VERB", 7)
    # A verb the bench knows but only sends is unknown as an incoming frame.
    await session.send({"verb": "heartbeat"})
    err = await session.expect("error")
    assert err["code"] == "UNKNOWN_VERB" and "re" not in err
    await session.send({"verb": "list_com", "id": 8})
    assert (await session.expect("com_list"))["re"] == 8


_BAD_FRAMES: list[tuple[str, Any, int | None]] = [
    ("not JSON", "this is not json", None),
    ("a JSON array", "[1, 2]", None),
    ("a JSON string", '"hello_ok"', None),
    ("a JSON number", "3", None),
    ("a binary message", b"\x00\x01", None),
    ("missing id", {"verb": "list_com"}, None),
    ("id of the wrong type", {"verb": "list_adb", "id": "1"}, None),
    (
        "kill_conflicts of the wrong type",
        {"verb": "restart_adb", "id": 12, "kill_conflicts": "y"},
        12,
    ),
    (
        "open_channel without cid",
        {"verb": "open_channel", "csecret": "x" * 43, "channel_type": "tcp", "params": {}},
        None,
    ),
    ("no verb", {"id": 13}, 13),
]


async def test_bad_frames_are_answered_and_the_bench_survives(
    hub: HubDouble, bench: Launch
) -> None:
    b, session = await _connected(hub, bench)
    for label, raw, re in _BAD_FRAMES:
        await session.send(raw)
        err = await session.expect("error")
        assert err["code"] == "BAD_FRAME", label
        assert err.get("re") == re, label
    assert b.running(), b.output()
    await session.send({"verb": "list_com", "id": 20})
    assert (await session.expect("com_list"))["re"] == 20


async def test_a_bad_error_frame_gets_no_answer(hub: HubDouble, bench: Launch) -> None:
    """F§2: errors are never answered with errors."""
    _, session = await _connected(hub, bench)
    await session.send({"verb": "error", "message": "no code"})
    await session.send({"verb": "error", "code": "BAD_FRAME", "message": "your frame", "re": 3})
    await session.send({"verb": "list_com", "id": 21})
    reply = await session.recv()
    assert reply["verb"] == "com_list" and reply["re"] == 21


# ── §4 device lists ──────────────────────────────────────────────────


async def test_device_lists_answer_with_the_request_id(hub: HubDouble, bench: Launch) -> None:
    _, session = await _connected(hub, bench)
    await session.send({"verb": "list_adb", "id": 5})
    reply = await session.expect("adb_list")
    assert reply["re"] == 5 and reply["devices"] == ["EXAMPLE0001"]
    await session.send({"verb": "restart_adb", "id": 6, "kill_conflicts": False})
    reply = await session.expect("adb_list")
    assert reply["re"] == 6


# ── §5 channels: tcp ─────────────────────────────────────────────────


async def _echo_server() -> tuple[asyncio.Server, int]:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while data := await reader.read(65536):
                writer.write(data)
                await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, int(server.sockets[0].getsockname()[1])


async def test_tcp_channel_reaches_the_configured_adb_port(hub: HubDouble, bench: Launch) -> None:
    server, port = await _echo_server()
    try:
        _, session = await _connected(hub, bench, adb_port=port)
        cid, csecret = await _open(session, "tcp", {"target": "127.0.0.1:5037"})
        dial = await hub.dialback(cid)
        assert dial.headers.get(TOKEN_HEADER) == TOKEN
        assert dial.headers.get(CSECRET_HEADER) == csecret
        await dial.ws.send(b"host:version")
        assert await recv_bytes(dial.ws, 12) == b"host:version"
        dial.done.set()
    finally:
        server.close()


async def test_tcp_channel_refusals_carry_codes(hub: HubDouble, bench: Launch) -> None:
    _, session = await _connected(hub, bench, adb_port=closed_port())
    cid, _ = await _open(session, "tcp", {"target": "192.0.2.10:22"})
    assert (await _channel_error(session, cid))["code"] == "BENCH_PARAM_REJECTED"
    cid, _ = await _open(session, "tcp", {"target": "127.0.0.1:5037"})
    assert (await _channel_error(session, cid))["code"] == "ADB_SERVER_UNREACHABLE"
    cid, _ = await _open(session, "usb", {})
    assert (await _channel_error(session, cid))["code"] == "BENCH_PARAM_REJECTED"


async def test_a_failed_dial_back_is_reported(hub: HubDouble, bench: Launch) -> None:
    """§5 rule 3: the bench reports a dial-back the hub refused."""
    server, port = await _echo_server()
    try:
        _, session = await _connected(hub, bench, adb_port=port)
        hub.reject_dialback = True
        cid, _ = await _open(session, "tcp", {"target": "127.0.0.1:5037"})
        assert (await _channel_error(session, cid))["code"] == "BENCH_DIAL_BACK_FAILED"
    finally:
        server.close()


# ── §5 channels: serial ──────────────────────────────────────────────


async def test_serial_channel_on_the_loopback_port(hub: HubDouble, bench: Launch) -> None:
    pytest.importorskip("serial")
    _, session = await _connected(hub, bench)
    cid, csecret = await _open(session, "serial", {"com": "loop://", "baud": 115200})
    dial = await hub.dialback(cid)
    assert dial.headers.get(CSECRET_HEADER) == csecret
    await dial.ws.send(b"AT\r\n")
    assert await recv_bytes(dial.ws, 4) == b"AT\r\n"
    dial.done.set()


async def test_serial_channel_refusals_carry_codes(hub: HubDouble, bench: Launch) -> None:
    _, session = await _connected(hub, bench)
    cid, _ = await _open(session, "serial", {"com": "socket://192.0.2.10:23", "baud": 115200})
    assert (await _channel_error(session, cid))["code"] == "BENCH_PARAM_REJECTED"
    cid, _ = await _open(session, "serial", {"com": "COM3", "baud": "fast"})
    assert (await _channel_error(session, cid))["code"] == "BENCH_PARAM_REJECTED"


# ── §7 channels: job ─────────────────────────────────────────────────


async def _job(
    hub: HubDouble, session: Session, request: dict[str, Any], hint: str | None = None
) -> list[dict[str, Any]]:
    cid, _ = await _open(session, "job", {"op": hint or str(request.get("op"))})
    dial = await hub.dialback(cid)
    try:
        await dial.ws.send(job_control(request))
        return await JobReader(dial.ws).until_done()
    finally:
        dial.done.set()


async def test_job_runs_a_tool_op_without_hardware(
    hub: HubDouble, bench: Launch, tmp_path: Path
) -> None:
    """A fastboot op against a fake fastboot executable."""
    fastboot = fake_tool(tmp_path / "tools", "fastboot")
    _, session = await _connected(hub, bench, fastboot_path=fastboot)
    events = await _job(hub, session, {"op": "devices"})
    assert events[0]["ev"] == "accepted"
    done = events[-1]
    assert done["ok"] is True and done["code"] == ""
    assert "EXAMPLE0001" in done["stdout"]
    events = await _job(hub, session, {"op": "getvar", "name": "product"})
    assert events[-1]["ok"] is True
    assert "EXAMPLE-VALUE" in events[-1]["stderr"]


async def test_job_refusals_are_done_frames_with_codes(
    hub: HubDouble, bench: Launch, tmp_path: Path
) -> None:
    """§7: a stray key, an int as a string, an unknown op, a request that
    does not match the channel's op — each is a failed done with a code."""
    fastboot = fake_tool(tmp_path / "tools", "fastboot")
    _, session = await _connected(hub, bench, fastboot_path=fastboot)
    cases: list[tuple[dict[str, Any], str | None, str]] = [
        ({"op": "devices", "verbose": True}, None, "BENCH_PARAM_REJECTED"),
        ({"op": "getvar", "name": "product", "nmae": "x"}, None, "BENCH_PARAM_REJECTED"),
        ({"op": "no-such-op"}, None, "BENCH_OP_UNKNOWN"),
        ({"op": "getvar"}, "devices", "BENCH_PARAM_REJECTED"),
        ({"op": "getvar", "name": "-w"}, None, "FLASH_VAR_REJECTED"),
    ]
    for request, hint, code in cases:
        (done,) = [e for e in await _job(hub, session, request, hint) if e["ev"] == "done"]
        assert (done["ok"], done["code"]) == (False, code), request


async def test_job_image_is_received_and_checked(
    hub: HubDouble, bench: Launch, tmp_path: Path
) -> None:
    """§7: an image op gets accepted, transfer progress, then its verdict.
    The digest is wrong on purpose: the bench must say so before it runs
    anything (the fake fastboot would accept any flash)."""
    fastboot = fake_tool(tmp_path / "tools", "fastboot")
    _, session = await _connected(hub, bench, fastboot_path=fastboot)
    image = b"\x7fELF" + bytes(4092)
    cid, _ = await _open(session, "job", {"op": "flash"})
    dial = await hub.dialback(cid)
    try:
        request = {
            "op": "flash",
            "partition": "boot_a",
            "size": len(image),
            "sha256": hashlib.sha256(b"something else").hexdigest(),
        }
        await dial.ws.send(job_control(request))
        await dial.ws.send(b"D" + len(image).to_bytes(4, "big") + image)
        await dial.ws.send(b"D\x00\x00\x00\x00")
        events = await JobReader(dial.ws).until_done()
    finally:
        dial.done.set()
    kinds = [e["ev"] for e in events]
    assert kinds[0] == "accepted" and "progress" in kinds
    assert any(e.get("phase") == "transfer" for e in events)
    assert (events[-1]["ok"], events[-1]["code"]) == (False, "FLASH_IMAGE_CORRUPT")


async def test_job_channel_refused_without_fastboot(hub: HubDouble, bench: Launch) -> None:
    """§5: a fastboot op on a bench without fastboot -> channel_error."""
    _, session = await _connected(hub, bench)
    cid, _ = await _open(session, "job", {"op": "flash"})
    assert (await _channel_error(session, cid))["code"] == "FASTBOOT_UNAVAILABLE"


def test_the_fake_tools_never_reach_a_real_adb_server() -> None:
    """Guard for the harness itself: closed_port() really is closed, so a
    bench pointed at it cannot be talking to the host's adb server."""
    port = closed_port()
    assert port != 5037
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(2)
        assert sock.connect_ex(("127.0.0.1", port)) != 0


async def test_the_bench_exits_cleanly_when_stopped(hub: HubDouble, bench: Launch) -> None:
    """Not a protocol rule; keeps the suite from leaving processes behind.
    Terminate only: Bench.stop() would fall back to kill, and then this test
    would pass for a bench that ignores the request."""
    b, _ = await _connected(hub, bench)
    b.proc.terminate()
    assert await wait_exit(b, 15), b.output()
