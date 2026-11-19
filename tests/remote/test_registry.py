"""AgentRegistry + dial-back correlation tests (ADR-050/051)."""

from __future__ import annotations

from typing import Any

import pytest

from devicehands.remote.bench_ops import OpSpec
from devicehands.remote.protocol import ADB_TARGET, ChannelType
from devicehands.remote.registry import (
    AgentConnection,
    AgentRegistry,
    ChannelOpenError,
    get_agent_registry,
    reset_agent_registry,
)


class _DummyChannel:
    async def recv(self) -> bytes:
        return b""

    async def send(self, data: bytes) -> None:
        return None

    async def aclose(self) -> None:
        return None


def _conn(reg: AgentRegistry, agent_id: str, send=None) -> AgentConnection:
    async def _noop(_m: dict[str, Any]) -> None:
        return None

    return AgentConnection(
        agent_id=agent_id,
        name=agent_id,
        send_control=send or _noop,
        registry=reg,
    )


async def test_register_returns_increasing_epoch_per_agent():
    reg = AgentRegistry()
    e1 = await reg.register(_conn(reg, "a"))
    e2 = await reg.register(_conn(reg, "a"))  # same id reconnect
    assert e2 > e1
    assert reg.agent_count == 1


async def test_unregister_compare_and_clear():
    reg = AgentRegistry()
    c1 = _conn(reg, "a")
    e1 = await reg.register(c1)
    # a newer connection replaces it
    await reg.register(_conn(reg, "a"))
    # stale teardown with the OLD epoch must NOT evict the new connection
    removed = await reg.unregister("a", e1)
    assert removed is False
    assert reg.agent_count == 1


async def test_unregister_current_epoch_removes():
    reg = AgentRegistry()
    e = await reg.register(_conn(reg, "a"))
    assert await reg.unregister("a", e) is True
    assert reg.agent_count == 0
    assert reg.get("a") is None


async def test_pending_resolves_future():
    reg = AgentRegistry()
    fut = reg.register_pending("cid1", "sec1")
    assert reg.pending_count == 1
    ch = _DummyChannel()
    assert reg.resolve_pending("cid1", ch, "sec1") is True
    assert fut.done() and fut.result() is ch
    reg.discard_pending("cid1")
    assert reg.pending_count == 0


async def test_fail_pending_raises_the_agents_reason():
    """issue #4: when the agent says it cannot open the channel, the waiter must
    fail NOW with that reason — not sit out the full dial-back timeout while the
    caller's writes drain into a socket that will never carry them."""
    reg = AgentRegistry()
    fut = reg.register_pending("cid1", "sec1")
    assert reg.fail_pending("cid1", "cannot open COM4: Access is denied") is True
    assert fut.done()
    with pytest.raises(ChannelOpenError, match="Access is denied"):
        fut.result()


async def test_fail_pending_unknown_or_settled_cid_is_false():
    reg = AgentRegistry()
    assert reg.fail_pending("nope", "boom") is False
    fut = reg.register_pending("cid1", "sec1")
    assert reg.resolve_pending("cid1", _DummyChannel(), "sec1") is True
    assert reg.fail_pending("cid1", "too late") is False
    assert fut.exception() is None


async def test_fail_pending_has_a_reason_even_when_the_agent_sent_none():
    reg = AgentRegistry()
    fut = reg.register_pending("cid1", "sec1")
    assert reg.fail_pending("cid1", "") is True
    assert str(fut.exception())  # never an empty error string


async def test_resolve_unknown_cid_is_false():
    reg = AgentRegistry()
    assert reg.resolve_pending("nope", _DummyChannel(), "sec") is False


async def test_resolve_wrong_csecret_is_false():
    """DEBT-084: a dial-back with the right cid but the wrong per-channel secret
    must NOT resolve the channel, and must leave the future open for the real
    dial-back."""
    reg = AgentRegistry()
    fut = reg.register_pending("cid1", "right")
    assert reg.resolve_pending("cid1", _DummyChannel(), "wrong") is False
    assert not fut.done()
    # the legitimate dial-back (correct secret) still resolves it
    ch = _DummyChannel()
    assert reg.resolve_pending("cid1", ch, "right") is True
    assert fut.result() is ch


async def test_resolve_missing_csecret_is_false():
    reg = AgentRegistry()
    fut = reg.register_pending("cid1", "right")
    assert reg.resolve_pending("cid1", _DummyChannel(), None) is False
    assert reg.resolve_pending("cid1", _DummyChannel(), "") is False
    assert not fut.done()


async def test_resolve_non_ascii_csecret_rejects_without_raising():
    """A non-ASCII presented secret must reject cleanly, NOT raise TypeError out
    of resolve_pending (hmac.compare_digest forbids non-ASCII str; we compare as
    bytes). The future stays open for the legitimate dial-back."""
    reg = AgentRegistry()
    fut = reg.register_pending("cid1", "right")
    assert reg.resolve_pending("cid1", _DummyChannel(), "café") is False
    assert reg.resolve_pending("cid1", _DummyChannel(), "日本語") is False
    assert not fut.done()
    # the real secret still resolves
    ch = _DummyChannel()
    assert reg.resolve_pending("cid1", ch, "right") is True
    assert fut.result() is ch


async def test_open_data_channel_registers_cid_before_signaling():
    """Checklist #1: the cid placeholder MUST be registered before
    open_channel is sent, so a dial-back that races the await still finds it.
    We simulate an instant dial-back inside send_control."""
    reg = AgentRegistry()
    seen: dict[str, Any] = {}

    async def send_control(m: dict[str, Any]) -> None:
        cid = m["cid"]
        # at this point the pending future MUST already exist, and the frame
        # MUST carry the per-channel secret the dial-back has to present
        assert m["csecret"]
        seen["resolved"] = reg.resolve_pending(cid, _DummyChannel(), m["csecret"])

    conn = _conn(reg, "a", send=send_control)
    dc = await conn.open_data_channel(
        ctype=ChannelType.TCP,
        params={"target": ADB_TARGET},
        timeout=2.0,
    )
    assert isinstance(dc, _DummyChannel)
    assert seen["resolved"] is True
    assert reg.pending_count == 0  # discarded in finally


def test_singleton_reset():
    r1 = get_agent_registry()
    reset_agent_registry()
    r2 = get_agent_registry()
    assert r1 is not r2


# ── device enumeration cache (P4-deferred → enumeration increment) ────


async def test_request_device_list_asks_for_both_lists_with_fresh_ids() -> None:
    """COM ports are asked for unconditionally: nothing in the op table says
    whether a bench has a serial port, and gating on a capability left
    com_ports empty on every bench once."""
    reg = AgentRegistry()
    sent: list[dict[str, Any]] = []

    async def send(m: dict[str, Any]) -> None:
        sent.append(m)

    conn = _conn(reg, "a", send=send)
    await conn.request_device_list()
    await conn.request_adb_restart(kill_conflicts=True)
    assert [m["verb"] for m in sent] == ["list_adb", "list_com", "restart_adb"]
    ids = [m["id"] for m in sent]
    assert len(set(ids)) == 3 and ids == sorted(ids)


async def test_replies_apply_by_request_id_and_never_go_backwards() -> None:
    """sequences.md §4: a late answer to an older request must not
    overwrite a newer one, and a reply to nothing we asked is dropped."""
    reg = AgentRegistry()
    sent: list[dict[str, Any]] = []

    async def send(m: dict[str, Any]) -> None:
        sent.append(m)

    conn = _conn(reg, "a", send=send)
    await conn.request_device_list()
    await conn.request_device_list()
    old_adb, _, new_adb, new_com = (m["id"] for m in sent)
    newer = {"verb": "adb_list", "re": new_adb, "devices": ["new"], "conflicts": []}
    older = {"verb": "adb_list", "re": old_adb, "devices": ["old"], "conflicts": []}
    assert conn.apply_reply(newer) is True
    assert conn.apply_reply(older) is False
    assert conn.adb_devices == ["new"]
    stray = {"verb": "adb_list", "re": 999, "devices": ["stray"], "conflicts": ["x pid=1"]}
    assert conn.apply_reply(stray) is False
    # an adb id answered as a com list is not a match either
    assert conn.apply_reply({"verb": "com_list", "re": new_adb, "ports": []}) is False
    ports = [{"port": "COM3", "desc": ""}]
    assert conn.apply_reply({"verb": "com_list", "re": new_com, "ports": ports}) is True
    assert conn.com_ports == ports
    # each request is answered once
    assert conn.apply_reply(newer) is False


async def test_adb_list_conflicts_are_stored_and_cleared() -> None:
    reg = AgentRegistry()
    sent: list[dict[str, Any]] = []

    async def send(m: dict[str, Any]) -> None:
        sent.append(m)

    conn = _conn(reg, "a", send=send)
    await conn.request_device_list()
    await conn.request_device_list()
    first, second = sent[0]["id"], sent[2]["id"]
    conn.apply_reply({"verb": "adb_list", "re": first, "devices": [], "conflicts": ["x_adb pid=9"]})
    assert conn.adb_conflicts == ["x_adb pid=9"]
    conn.apply_reply({"verb": "adb_list", "re": second, "devices": ["s1"], "conflicts": []})
    assert conn.adb_conflicts == []


def test_capabilities_follow_from_the_op_table() -> None:
    reg = AgentRegistry()

    async def _noop(_m: dict[str, Any]) -> None:
        return None

    def conn(ops: dict[str, OpSpec]) -> AgentConnection:
        return AgentConnection(agent_id="a", name="a", send_control=_noop, registry=reg, ops=ops)

    assert conn({}).caps == ["adb"]
    host = {"usb-list": OpSpec("usb-list", group="host")}
    assert conn(host).caps == ["adb"]
    both = {**host, "devices": OpSpec("devices", group="fastboot")}
    assert conn(both).caps == ["adb", "fastboot"]
    assert conn(both).ops_reported is True
    assert conn(both).flash_partitions == []


async def test_channel_error_counts_only_from_the_owning_connection() -> None:
    """sequences.md §5 rule 4 (v1: any connection could fail any cid)."""
    reg = AgentRegistry()
    owner, other = object(), object()
    fut = reg.register_pending("cid1", "sec1", owner=owner)
    assert reg.fail_pending("cid1", "x", code="SERIAL_PORT_BUSY", owner=other) is False
    assert not fut.done()
    assert reg.fail_pending("cid1", "busy", code="SERIAL_PORT_BUSY", owner=owner) is True
    with pytest.raises(ChannelOpenError) as e:
        fut.result()
    assert e.value.code == "SERIAL_PORT_BUSY"


async def test_a_gone_connection_fails_its_waiting_opens() -> None:
    reg = AgentRegistry()
    owner, other = object(), object()
    mine = reg.register_pending("cid1", "s", owner=owner)
    theirs = reg.register_pending("cid2", "s", owner=other)
    assert reg.fail_owned(owner, "gone") == 1
    assert isinstance(mine.exception(), ChannelOpenError)
    assert not theirs.done()


async def test_list_agents_includes_device_cache():
    reg = AgentRegistry()
    conn = _conn(reg, "a")
    conn.adb_devices = ["serial-1"]
    conn.com_ports = [{"port": "COM3", "desc": "USB serial"}]
    await reg.register(conn)
    a = reg.list_agents()[0]
    assert a["adb_devices"] == ["serial-1"]
    assert a["com_ports"] == [{"port": "COM3", "desc": "USB serial"}]
