"""/bench/connect + /bench/channel: handshake, auth and session rules (protocol v2).

Uses Starlette's in-process TestClient WebSocket (no real network, no
`websockets` dependency). The adb forwarder is pointed at an ephemeral port
(DHANDS_ADB_FORWARD_PORT=0) so binding 5037 never clashes during tests.
Rules referenced as § are protocol/v2/sequences.md.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from devicehands.api.agent_route import _WsDataChannel
from devicehands.api.server import create_app
from devicehands.remote import protocol as p

_CID = "0123456789abcdef0123456789abcdef"


class _FakeWs:
    def __init__(self, frames: list[dict[str, Any]]) -> None:
        self._frames = list(frames)
        self.sent: list[bytes] = []

    async def receive(self) -> dict[str, Any]:
        if self._frames:
            return self._frames.pop(0)
        return {"type": "websocket.disconnect"}

    async def send_bytes(self, data: bytes) -> None:
        self.sent.append(data)


async def test_wsdatachannel_recv_send_close() -> None:
    ws = _FakeWs([{"bytes": b"abc"}, {"text": "ctrl"}])
    ch = _WsDataChannel(ws)
    assert await ch.recv() == b"abc"  # binary frame passes through
    assert await ch.recv() == b""  # a text frame on a data connection = its end
    assert await ch.recv() == b""  # disconnect = EOF
    await ch.send(b"out")
    assert ws.sent == [b"out"]
    waiter = asyncio.create_task(ch.wait_closed())
    await ch.aclose()
    await asyncio.wait_for(waiter, timeout=1.0)  # aclose unblocks wait_closed


@pytest.fixture(autouse=True)
def _ephemeral_forward_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DHANDS_ADB_FORWARD_PORT", "0")


def _hello(**kw: Any) -> str:
    fields: dict[str, Any] = dict(
        bench_id="agent-1", name="t", lang="python", build="", os="linux", ops=[], boards={}
    )
    fields.update(kw)
    return p.encode_control(p.hello(**fields))


def _recv(ws: Any) -> dict[str, Any]:
    frame: dict[str, Any] = json.loads(ws.receive_text())
    return frame


def _connect(c: TestClient, token: str | None = None) -> Any:
    headers = {p.TOKEN_HEADER: token} if token is not None else {}
    return c.websocket_connect(p.CONNECT_PATH, headers=headers)


def _handshake(ws: Any) -> list[dict[str, Any]]:
    """hello -> hello_ok, then the two device-list requests (§2)."""
    ws.send_text(_hello())
    ok = _recv(ws)
    assert ok == {"verb": "hello_ok", "proto": 2}
    reqs = [_recv(ws), _recv(ws)]
    assert [r["verb"] for r in reqs] == ["list_adb", "list_com"]
    return reqs


def test_hello_gets_hello_ok_when_no_token_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DHANDS_AGENT_TOKEN", raising=False)
    with TestClient(create_app()) as c:
        with _connect(c) as ws:
            _handshake(ws)


def test_correct_token_header_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DHANDS_AGENT_TOKEN", "s3cret")
    with TestClient(create_app()) as c:
        with _connect(c, "s3cret") as ws:
            _handshake(ws)


@pytest.mark.parametrize("token", [None, "wrong"])
def test_bad_or_missing_token_closes_1008_before_any_frame(
    monkeypatch: pytest.MonkeyPatch, token: str | None
) -> None:
    """§1: the token is a header and is checked first."""
    monkeypatch.setenv("DHANDS_AGENT_TOKEN", "s3cret")
    with TestClient(create_app()) as c:
        with pytest.raises(WebSocketDisconnect) as e:
            with _connect(c, token) as ws:
                ws.receive_text()
    assert e.value.code == 1008


def test_a_token_in_the_hello_body_counts_for_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """v1 carried the token in hello; v2 never reads a credential from a frame."""
    monkeypatch.setenv("DHANDS_AGENT_TOKEN", "s3cret")
    hello = json.loads(_hello())
    hello["token"] = "s3cret"
    with TestClient(create_app()) as c:
        with pytest.raises(WebSocketDisconnect) as e:
            with _connect(c) as ws:
                ws.send_text(json.dumps(hello))
                ws.receive_text()
    assert e.value.code == 1008


@pytest.mark.parametrize(
    ("first", "code"),
    [
        (p.encode_control(p.heartbeat()), p.UNKNOWN_VERB),
        ('{"verb": "teleport"}', p.UNKNOWN_VERB),
        ("not json", p.BAD_FRAME),
        ('{"v": 1, "verb": "hello", "agent_id": "a"}', p.BAD_FRAME),
    ],
)
def test_a_first_frame_that_is_not_a_good_hello_is_answered_then_closed(
    monkeypatch: pytest.MonkeyPatch, first: str, code: str
) -> None:
    """§2: error frame, then close 1002."""
    monkeypatch.delenv("DHANDS_AGENT_TOKEN", raising=False)
    with TestClient(create_app()) as c:
        with pytest.raises(WebSocketDisconnect) as e:
            with _connect(c) as ws:
                ws.send_text(first)
                err = _recv(ws)
                assert err["verb"] == "error" and err["code"] == code
                ws.receive_text()
    assert e.value.code == 1002


@pytest.mark.parametrize(("lo", "hi"), [(1, 1), (3, 5)])
def test_version_mismatch_is_refused_with_supported_range(
    monkeypatch: pytest.MonkeyPatch, lo: int, hi: int
) -> None:
    monkeypatch.delenv("DHANDS_AGENT_TOKEN", raising=False)
    from devicehands.remote.registry import get_agent_registry

    with TestClient(create_app()) as c:
        with pytest.raises(WebSocketDisconnect) as e:
            with _connect(c) as ws:
                ws.send_text(_hello(proto_min=lo, proto_max=hi))
                err = _recv(ws)
                assert err["code"] == p.PROTO_VERSION_UNSUPPORTED
                assert err["supported"] == {"min": p.PROTO_MIN, "max": p.PROTO_MAX}
                ws.receive_text()
        assert get_agent_registry().agent_count == 0
    assert e.value.code == 1002


def test_a_wider_bench_range_negotiates_the_hubs_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DHANDS_AGENT_TOKEN", raising=False)
    with TestClient(create_app()) as c:
        with _connect(c) as ws:
            ws.send_text(_hello(proto_min=1, proto_max=9))
            assert _recv(ws) == {"verb": "hello_ok", "proto": p.PROTO_MAX}
            agent = c.get("/agent/status").json()["agents"][0]
    assert agent["proto"] == agent["version"] == p.PROTO_MAX
    assert agent["impl"] == {"lang": "python", "build": ""}
    assert agent["os"] == "linux"


def test_bad_frames_in_session_are_answered_and_the_session_goes_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """framing.md §2: UNKNOWN_VERB / BAD_FRAME, re when the frame had an id;
    a broken error frame is not answered."""
    monkeypatch.delenv("DHANDS_AGENT_TOKEN", raising=False)
    with TestClient(create_app()) as c:
        with _connect(c) as ws:
            _handshake(ws)
            ws.send_text('{"verb": "list_usb", "id": 9}')
            assert _recv(ws) == {
                "verb": "error",
                "code": p.UNKNOWN_VERB,
                "message": "unknown verb 'list_usb'",
                "re": 9,
            }
            ws.send_text(_hello())  # hello is only the first frame
            assert _recv(ws)["code"] == p.UNKNOWN_VERB
            ws.send_bytes(b"\x00")
            assert _recv(ws)["code"] == p.BAD_FRAME
            ws.send_text('{"verb": "adb_list", "re": 1}')
            assert _recv(ws)["code"] == p.BAD_FRAME
            ws.send_text('{"verb": "error", "message": "no code"}')  # not answered
            ws.send_text('{"verb": "teleport"}')
            assert _recv(ws)["code"] == p.UNKNOWN_VERB  # the next answer is this one
            assert len(c.get("/agent/status").json()["agents"]) == 1


def test_channel_unknown_cid_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DHANDS_AGENT_TOKEN", raising=False)
    with TestClient(create_app()) as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect(f"{p.CHANNEL_PATH}?cid=does-not-exist") as ws:
                ws.receive_bytes()


def test_channel_requires_cid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DHANDS_AGENT_TOKEN", raising=False)
    with TestClient(create_app()) as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect(p.CHANNEL_PATH) as ws:
                ws.receive_bytes()


def _capture_resolve(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    from devicehands.remote.registry import AgentRegistry

    captured: dict[str, Any] = {}

    def fake_resolve(self: Any, cid: str, channel: Any, csecret: str | None) -> bool:
        captured["cid"] = cid
        captured["csecret"] = csecret
        return False  # reject → route closes the WS → WebSocketDisconnect

    monkeypatch.setattr(AgentRegistry, "resolve_pending", fake_resolve)
    return captured


def test_channel_reads_csecret_from_header(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-055: the dial-back secret rides a header so it never lands in the
    access log's request line."""
    monkeypatch.delenv("DHANDS_AGENT_TOKEN", raising=False)
    captured = _capture_resolve(monkeypatch)
    with TestClient(create_app()) as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect(
                f"{p.CHANNEL_PATH}?cid=abc", headers={p.CSECRET_HEADER: "xyz"}
            ) as ws:
                ws.receive_bytes()
    assert captured == {"cid": "abc", "csecret": "xyz"}


def test_channel_token_from_header_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DHANDS_AGENT_TOKEN", "s3cret")
    captured = _capture_resolve(monkeypatch)
    with TestClient(create_app()) as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect(
                f"{p.CHANNEL_PATH}?cid=abc",
                headers={p.TOKEN_HEADER: "s3cret", p.CSECRET_HEADER: "xyz"},
            ) as ws:
                ws.receive_bytes()
    # got past the token gate — a rejected token never reaches resolve_pending
    assert captured == {"cid": "abc", "csecret": "xyz"}


def test_channel_bad_token_header_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DHANDS_AGENT_TOKEN", "s3cret")
    captured = _capture_resolve(monkeypatch)
    with TestClient(create_app()) as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect(
                f"{p.CHANNEL_PATH}?cid=abc", headers={p.TOKEN_HEADER: "wrong"}
            ) as ws:
                ws.receive_bytes()
    assert captured == {}  # closed before the registry was consulted


def test_channel_query_credentials_are_not_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """framing.md §1: v1's query-string fallback is gone."""
    monkeypatch.setenv("DHANDS_AGENT_TOKEN", "s3cret")
    captured = _capture_resolve(monkeypatch)
    with TestClient(create_app()) as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect(f"{p.CHANNEL_PATH}?cid=abc&token=s3cret&csecret=xyz") as ws:
                ws.receive_bytes()
    assert captured == {}  # no token header: refused at the gate
    monkeypatch.delenv("DHANDS_AGENT_TOKEN")
    with TestClient(create_app()) as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect(f"{p.CHANNEL_PATH}?cid=abc&csecret=xyz") as ws:
                ws.receive_bytes()
    assert captured == {"cid": "abc", "csecret": None}


def test_the_v1_paths_are_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DHANDS_AGENT_TOKEN", raising=False)
    with TestClient(create_app()) as c:
        for path in ("/agent/connect", "/agent/channel?cid=abc"):
            with pytest.raises(WebSocketDisconnect):
                with c.websocket_connect(path) as ws:
                    ws.receive_text()


def test_agent_survives_forwarder_bind_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-051 hub-side robustness: if the adb forwarder can't bind its port,
    the bench session must STILL complete the handshake (hello_ok) and
    register — not get torn down before hello_ok and trapped in a reconnect
    loop."""
    monkeypatch.delenv("DHANDS_AGENT_TOKEN", raising=False)
    from devicehands.remote.registry import get_agent_registry

    class _BoomForwarder:
        async def attach(self):
            raise OSError("[Errno 98] address already in use")

    monkeypatch.setattr("devicehands.api.agent_route.get_adb_forwarder", lambda: _BoomForwarder())

    with TestClient(create_app()) as c:
        with _connect(c) as ws:
            _handshake(ws)
            assert get_agent_registry().agent_count == 1
            # ...and the failure stays observable: the forwarder reports unbound
            from devicehands.remote.forwarder import forwarder_status

            assert forwarder_status()["adb"]["bound"] is False


def test_heartbeat_timeout_tears_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """§3: after hello, a bench that sends nothing is dropped once
    HEARTBEAT_TIMEOUT_S elapses — and the registry is cleaned up (finally)."""
    monkeypatch.delenv("DHANDS_AGENT_TOKEN", raising=False)
    monkeypatch.setattr("devicehands.api.agent_route.HEARTBEAT_TIMEOUT_S", 0.3)
    from devicehands.remote.registry import get_agent_registry

    with TestClient(create_app()) as c:
        with pytest.raises(WebSocketDisconnect):
            with _connect(c) as ws:
                _handshake(ws)
                while True:
                    ws.receive_text()
        assert get_agent_registry().agent_count == 0


def test_a_hello_that_never_comes_is_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """§2: 10 s for the hello (shortened here)."""
    monkeypatch.delenv("DHANDS_AGENT_TOKEN", raising=False)
    monkeypatch.setattr("devicehands.api.agent_route.HELLO_TIMEOUT_S", 0.3)
    with TestClient(create_app()) as c:
        with pytest.raises(WebSocketDisconnect) as e:
            with _connect(c) as ws:
                ws.receive_text()
    assert e.value.code == 1002


# ── GET /agent/status (Connection Center backend) ─────────────────────


def test_agent_status_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DHANDS_AGENT_TOKEN", raising=False)
    with TestClient(create_app()) as c:
        body = c.get("/agent/status").json()
    assert body["agents"] == []
    assert body["forwarders"]["adb"]["bound"] is False
    assert "serial" in body["forwarders"]
    assert body["forwarders"]["serial"]["configured"] is False


def test_agent_status_shows_connected_bench(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DHANDS_AGENT_TOKEN", raising=False)
    with TestClient(create_app()) as c:
        with _connect(c) as ws:
            _handshake(ws)
            body = c.get("/agent/status").json()
    assert len(body["agents"]) == 1
    agent = body["agents"][0]
    assert agent["agent_id"] == "agent-1"
    assert agent["current"] is True
    # keys the status page reads, still present; the v2 values
    assert agent["caps"] == ["adb"] and agent["ops"] == [] and agent["ops_reported"] is True
    assert agent["flash_partitions"] == []
    assert "adb_devices" in agent and "com_ports" in agent
    assert body["forwarders"]["adb"]["bound"] is True


def test_agent_status_serial_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DHANDS_AGENT_TOKEN", raising=False)
    monkeypatch.setenv("DHANDS_AGENT_SERIAL_COM", "COM5")
    monkeypatch.setenv("DHANDS_AGENT_SERIAL_BAUD", "115200")
    with TestClient(create_app()) as c:
        body = c.get("/agent/status").json()
    assert body["forwarders"]["serial"]["configured"] is True
    assert body["forwarders"]["serial"]["com"] == "COM5"
    assert body["forwarders"]["serial"]["baud"] == 115200


# ── device enumeration (§4) ─────────────────────────────────────────────


def _wait_for(check: Callable[[], bool], what: str) -> None:
    """The route runs on the server's own loop — poll until it lands."""
    for _ in range(200):
        if check():
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what}")


def test_device_replies_reach_the_status_by_request_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DHANDS_AGENT_TOKEN", raising=False)
    with TestClient(create_app()) as c:
        with _connect(c) as ws:
            list_adb, list_com = _handshake(ws)
            ws.send_text(
                p.encode_control(
                    p.com_list(re_id=list_com["id"], ports=[{"port": "COM3", "desc": ""}])
                )
            )
            ws.send_text(
                p.encode_control(
                    p.adb_list(re_id=list_adb["id"], devices=["EXAMPLE0001"], conflicts=[])
                )
            )

            def agent() -> dict[str, Any]:
                return c.get("/agent/status").json()["agents"][0]

            _wait_for(lambda: agent()["adb_devices"] == ["EXAMPLE0001"], "adb_list")
            assert agent()["com_ports"] == [{"port": "COM3", "desc": ""}]


# ── POST /api/agent/adb/restart ────────────────────────────────────────


def test_adb_restart_sends_verb_to_current_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DHANDS_AGENT_TOKEN", raising=False)
    with TestClient(create_app()) as c:
        with _connect(c) as ws:
            reqs = _handshake(ws)
            body = c.post("/api/agent/adb/restart").json()
            assert body["ok"] is True
            assert body["agent_id"] == "agent-1"
            # the bench receives the restart request, with a fresh id
            frame = _recv(ws)
            assert frame["verb"] == p.Verb.RESTART_ADB.value
            assert frame["kill_conflicts"] is False
            assert frame["id"] > max(r["id"] for r in reqs)


def test_adb_restart_409_when_no_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DHANDS_AGENT_TOKEN", raising=False)
    with TestClient(create_app()) as c:
        r = c.post("/api/agent/adb/restart")
    assert r.status_code == 409


def test_adb_restart_kill_conflicts_flag_in_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DHANDS_AGENT_TOKEN", raising=False)
    with TestClient(create_app()) as c:
        with _connect(c) as ws:
            _handshake(ws)
            body = c.post("/api/agent/adb/restart?kill_conflicts=true").json()
            assert body["kill_conflicts"] is True
            frame = _recv(ws)
            assert frame["verb"] == p.Verb.RESTART_ADB.value
            assert frame["kill_conflicts"] is True


# ── channel_error (§5) ─────────────────────────────────────────────────


def test_channel_error_frame_is_dispatched_to_fail_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    """issue #4: `channel_error` must wake the waiting forwarder immediately
    with the bench's code and reason, instead of leaving it to time out on a
    dial-back that will never come. The session must survive the frame, and
    the route says which connection the frame came from (§5 rule 4)."""
    monkeypatch.delenv("DHANDS_AGENT_TOKEN", raising=False)
    from devicehands.remote.registry import AgentRegistry

    seen: list[tuple[str, str, str, str | None]] = []

    def _spy(self: Any, cid: str, reason: str, *, code: str = "", owner: Any = None) -> bool:
        seen.append((cid, code, reason, owner.agent_id if owner else None))
        return True

    monkeypatch.setattr(AgentRegistry, "fail_pending", _spy)

    with TestClient(create_app()) as c:
        with _connect(c) as ws:
            _handshake(ws)
            ws.send_text(
                p.encode_control(
                    p.channel_error(
                        cid=_CID,
                        code="SERIAL_PORT_BUSY",
                        reason="cannot open COM4: Access is denied",
                    )
                )
            )
            _wait_for(lambda: bool(seen), "channel_error")
            assert seen == [
                (_CID, "SERIAL_PORT_BUSY", "cannot open COM4: Access is denied", "agent-1")
            ]
            # an unopenable channel is not a session-level failure
            assert len(c.get("/agent/status").json()["agents"]) == 1


def test_a_channel_error_without_code_is_a_bad_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DHANDS_AGENT_TOKEN", raising=False)
    with TestClient(create_app()) as c:
        with _connect(c) as ws:
            _handshake(ws)
            ws.send_text(json.dumps({"verb": "channel_error", "cid": _CID, "reason": "x"}))
            assert _recv(ws)["code"] == p.BAD_FRAME
