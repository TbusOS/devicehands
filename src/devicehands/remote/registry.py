"""Connected-bench registry + per-channel dial-back correlation (ADR-050/051).

The hub holds one AgentConnection per connected bench. When a forwarder needs
a data channel it asks the AgentConnection to open one: the connection
registers a pending future keyed by a fresh cid (with a per-channel secret —
DEBT-084) BEFORE sending `open_channel` on the signaling WS (so a fast dial-back
can never miss the cid — checklist #1), then awaits the dialed-back DataChannel.
The dial-back must present the secret, which only the owning bench received,
and a `channel_error` for the cid counts only when it arrives on that same
bench's signaling connection (protocol/v2/sequences.md section 5).

Device-list requests carry an `id`; the bench's reply carries it back as
`re`, so a late answer to an older request can never overwrite a newer one
(sequences.md section 4).

Registration uses a per-agent epoch so that a reconnect of the same agent_id
does NOT let a stale connection's teardown clobber the new one (checklist #4,
compare-and-clear).
"""

from __future__ import annotations

import asyncio
import hmac
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Protocol

from devicehands.remote import protocol
from devicehands.remote.bench_ops import OpSpec, caps_of
from devicehands.remote.protocol import ChannelType

_log = logging.getLogger(__name__)

# Unanswered device-list requests kept per connection. A bench that never
# answers must not grow the map without end; the oldest are forgotten.
_MAX_OUTSTANDING = 64


class ChannelOpenError(Exception):
    """The bench reported it could NOT open a requested data channel.

    Distinct from a dial-back timeout: this carries the bench's own reason
    (e.g. "cannot open COM4: Access is denied") and code (SERIAL_PORT_BUSY),
    so the hub log and the caller see WHY instead of "something took too
    long", and a retry rule can look at the code.
    """

    def __init__(self, reason: str, code: str = "") -> None:
        super().__init__(reason)
        self.code = code


class DataChannel(Protocol):
    """A bidirectional byte channel (one dialed-back data connection)."""

    async def recv(self) -> bytes:  # b"" == EOF
        ...

    async def send(self, data: bytes) -> None: ...

    async def aclose(self) -> None: ...


class ChannelOpener(Protocol):
    """What a forwarder needs from an agent: open one data channel."""

    async def open_data_channel(
        self,
        *,
        ctype: ChannelType,
        params: dict[str, Any],
        timeout: float,
    ) -> DataChannel: ...


SendControl = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class _Pending:
    """A pending data-channel dial-back: the future the forwarder awaits, plus
    the per-channel secret (DEBT-084) the dial-back must present to resolve it."""

    fut: asyncio.Future[DataChannel]
    csecret: str
    # The connection the open_channel went out on; None = not bound (tests
    # that drive the registry directly).
    owner: object | None = None


def settled_update(last: dict[str, Any] | None, build: str) -> dict[str, Any]:
    """The agent's last remote update, with a `pending` that is already over
    read as the outcome it is.

    The bench's update helper writes `pending` before it starts the new build
    and `updated` only after that build has connected — but the new build
    reads the record while it starts, before connecting, so its hello always
    says `pending` (/agent/status showed `pending` from 2026-10-16 on). A hello
    from the build the record was heading for IS that connection, so the
    update completed. `pending` on any other build (the helper died mid-swap
    and the old build came back) is left as reported."""
    rec = dict(last or {})
    if rec.get("outcome") == "pending" and build and rec.get("to_build") == build:
        rec["outcome"] = "updated"
        rec["reported"] = "pending"
    return rec


class AgentConnection:
    """One connected bench. Owns the signaling-WS send callable + metadata.
    Pending-channel futures live in the registry (keyed by the global cid).
    `agent_id` is the hello's `bench_id`; the attribute keeps its name because
    the status page and its callers read it."""

    def __init__(
        self,
        *,
        agent_id: str,
        name: str,
        send_control: SendControl,
        registry: AgentRegistry,
        ops: dict[str, OpSpec] | None = None,
        proto: int = protocol.PROTO_MAX,
        impl_lang: str = "python",
        os_name: str = "",
        boards: dict[str, dict[str, Any]] | None = None,
        build: str = "",
        last_update: dict[str, Any] | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.name = name
        # The protocol version negotiated for this session (hello_ok.proto).
        self.proto = proto
        self.impl_lang = impl_lang
        self.os_name = os_name
        # `build` is the bench's own build id ("" when unreadable). It is how
        # an update is confirmed after the restart (ADR-060).
        self.build = build
        # {"id", "outcome", "from_build", "to_build", "at"} of the bench's last
        # remote update, as the bench host's update helper recorded it.
        self.last_update: dict[str, Any] = settled_update(last_update, build)
        # No v2 hello carries a partition list: names are shape-checked by the
        # rule each `partition` parameter brings along. Empty = any
        # well-formed name, NOT "nothing allowed" (see the web partition
        # picker, which falls back to a generic list in that case).
        self.flash_partitions: list[str] = []
        # Per-board products (ADR-063 phase 2), as parsed by
        # bench_board.parse_agent_boards. Empty = a bench without them.
        self.boards: dict[str, dict[str, Any]] = dict(boards or {})
        # The bench's op table (ADR-059). Every v2 bench reports one, so it is
        # never a guess; what the bench can do follows from it alone.
        self.op_table: dict[str, OpSpec] = dict(ops or {})
        self.ops_reported = True
        self.caps: list[str] = caps_of(self.op_table)
        # Device-list requests: id -> which list it asks for; the highest id
        # applied per list. See apply_reply.
        self._next_id = 0
        self._outstanding: dict[int, str] = {}
        self._applied: dict[str, int] = {}
        self._send_control = send_control
        self._registry = registry
        self.epoch = 0
        # set by the route once the forwarder is attached (for teardown)
        self.forwarder: Any = None
        # last-reported device enumeration (updated by the signaling recv loop
        # from adb_list / com_list replies). Surfaced via GET /agent/status.
        self.adb_devices: list[str] = []
        # adb-flavoured foreign processes the agent saw while its device list
        # was empty — the exclusive-USB-interface takeover signature.
        self.adb_conflicts: list[str] = []
        self.com_ports: list[dict[str, Any]] = []

    @property
    def version(self) -> int:
        """The negotiated protocol version; the status page's old key."""
        return self.proto

    def _new_request(self, kind: str) -> int:
        self._next_id += 1
        self._outstanding[self._next_id] = kind
        while len(self._outstanding) > _MAX_OUTSTANDING:
            del self._outstanding[next(iter(self._outstanding))]
        return self._next_id

    async def request_device_list(self) -> None:
        """Fire-and-forget: ask the bench to (re)report its devices. The replies
        (adb_list / com_list) land on the signaling WS and update the cache. The
        web Connection Center polls /agent/status, so the next poll reflects it."""
        with suppress(Exception):
            await self._send_control(protocol.list_adb(req_id=self._new_request("adb")))
            # Asked unconditionally: COM ports are not a capability of the op
            # table, and gating on one left com_ports empty on every bench.
            await self._send_control(protocol.list_com(req_id=self._new_request("com")))

    def apply_reply(self, frame: dict[str, Any]) -> bool:
        """Update the device cache from an adb_list / com_list reply.

        Only a reply to a request this session sent counts, and only when it
        answers a newer request than the last one applied to the same list:
        the bench answers each request in its own task, so replies can arrive
        out of order. False when the reply was dropped."""
        kind = "adb" if frame.get("verb") == protocol.Verb.ADB_LIST.value else "com"
        re_id = frame.get("re")
        if not isinstance(re_id, int) or self._outstanding.get(re_id) != kind:
            _log.warning(
                "bench %s: %s reply re=%r answers no request of ours", self.agent_id, kind, re_id
            )
            return False
        del self._outstanding[re_id]
        if re_id < self._applied.get(kind, 0):
            return False  # an older request's answer, after a newer one's
        self._applied[kind] = re_id
        if kind == "adb":
            self.adb_devices = [str(d) for d in frame.get("devices") or []]
            self.adb_conflicts = [str(c) for c in frame.get("conflicts") or []]
        else:
            self.com_ports = [dict(p) for p in frame.get("ports") or []]
        return True

    async def request_adb_restart(self, *, kill_conflicts: bool = False) -> None:
        """Fire-and-forget: ask the agent to restart ITS local adb server and
        re-report devices (the adb_list reply updates the cache; poll
        /agent/status for the result). Unsticks a wedged host-side adb
        enumeration without anyone touching the agent machine. kill_conflicts
        additionally clears adb-flavoured foreign processes holding the
        exclusive USB interface (the agent decides what matches)."""
        with suppress(Exception):
            req_id = self._new_request("adb")
            await self._send_control(
                protocol.restart_adb(req_id=req_id, kill_conflicts=kill_conflicts)
            )

    async def open_data_channel(
        self,
        *,
        ctype: ChannelType,
        params: dict[str, Any],
        timeout: float,
    ) -> DataChannel:
        """Open one data channel and return the dialed-back DataChannel.

        Order is load-bearing (checklist #1): register the cid placeholder
        FIRST, send `open_channel` SECOND, so the bench's dial-back to
        /bench/channel?cid=<cid> always finds a waiting future."""
        cid = protocol.new_cid()
        csecret = protocol.new_csecret()
        fut = self._registry.register_pending(cid, csecret, owner=self)
        try:
            await self._send_control(
                protocol.open_channel(cid=cid, csecret=csecret, ctype=ctype, params=params)
            )
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._registry.discard_pending(cid)


class AgentRegistry:
    """Process-wide registry of connected agents + pending data channels.

    Runs entirely on the asyncio event loop. The `_lock` guards the agent
    map across the register/unregister critical sections; the pending map is
    touched only synchronously (no awaits between read and write) so it needs
    no lock.
    """

    def __init__(self) -> None:
        self._agents: dict[str, AgentConnection] = {}
        self._epoch: dict[str, int] = {}
        self._pending: dict[str, _Pending] = {}
        self._lock = asyncio.Lock()

    async def register(self, conn: AgentConnection) -> int:
        """Register (or replace) an agent. Returns this connection's epoch;
        teardown must pass it back to unregister() so a stale connection
        can't evict a newer one."""
        async with self._lock:
            epoch = self._epoch.get(conn.agent_id, 0) + 1
            self._epoch[conn.agent_id] = epoch
            conn.epoch = epoch
            self._agents[conn.agent_id] = conn
            return epoch

    async def unregister(self, agent_id: str, epoch: int) -> bool:
        """Compare-and-clear: only remove the agent if `epoch` is still the
        current one (else a reconnect already replaced it). Returns True if
        this call actually removed the entry."""
        async with self._lock:
            if self._epoch.get(agent_id) == epoch and agent_id in self._agents:
                del self._agents[agent_id]
                return True
            return False

    def get(self, agent_id: str) -> AgentConnection | None:
        return self._agents.get(agent_id)

    def current_agent(self) -> AgentConnection | None:
        """The agent to route device traffic to. P0 is single-agent, so this
        returns the most-recently-registered agent (or None). Multi-agent
        addressing — picking the agent by device — is DEBT-083 / P4."""
        if not self._agents:
            return None
        return max(self._agents.values(), key=lambda c: c.epoch)

    def list_agents(self) -> list[dict[str, Any]]:
        return [
            {
                "agent_id": c.agent_id,
                "name": c.name,
                "version": c.proto,
                "proto": c.proto,
                "impl": {"lang": c.impl_lang, "build": c.build},
                "os": c.os_name,
                "build": c.build,
                "last_update": c.last_update,
                "caps": c.caps,
                "flash_partitions": c.flash_partitions,
                "boards": c.boards,
                "ops": sorted(c.op_table),
                "ops_reported": c.ops_reported,
                "adb_devices": c.adb_devices,
                "adb_conflicts": c.adb_conflicts,
                "com_ports": c.com_ports,
            }
            for c in self._agents.values()
        ]

    async def request_device_refresh(self) -> None:
        """Fire-and-forget a device-list request to every connected agent."""
        for c in list(self._agents.values()):
            await c.request_device_list()

    @property
    def agent_count(self) -> int:
        return len(self._agents)

    # ── pending data-channel correlation (cid + per-channel secret) ──

    def register_pending(
        self, cid: str, csecret: str, *, owner: object | None = None
    ) -> asyncio.Future[DataChannel]:
        fut: asyncio.Future[DataChannel] = asyncio.get_running_loop().create_future()
        self._pending[cid] = _Pending(fut, csecret, owner)
        return fut

    def resolve_pending(self, cid: str, channel: DataChannel, csecret: str | None) -> bool:
        """Bind a dialed-back DataChannel to its pending cid. Returns False if
        the cid is unknown/expired OR the presented per-channel secret does not
        match the one minted at open_channel (DEBT-084 — constant-time compare).
        The caller closes the data WS on False."""
        pending = self._pending.get(cid)
        if pending is None or pending.fut.done():
            return False
        if not csecret:
            return False
        # Compare as BYTES: hmac.compare_digest rejects non-ASCII str inputs with
        # TypeError, and csecret is attacker-controllable (dial-back query param).
        # Bytes have no such restriction, so a malformed secret reject cleanly
        # instead of throwing past the caller's ws.close(1008). utf-8 with
        # errors="replace" can never raise on the presented side.
        stored = pending.csecret.encode("utf-8")
        presented = csecret.encode("utf-8", "replace")
        if not hmac.compare_digest(stored, presented):
            return False
        pending.fut.set_result(channel)
        return True

    def fail_pending(
        self, cid: str, reason: str, *, code: str = "", owner: object | None = None
    ) -> bool:
        """Fail a pending dial-back because the bench said it could not open the
        channel (`channel_error` verb). Returns True if a waiter was woken.

        Why it matters: without it the ONLY signal of a failed open is the
        dial-back timeout, so the forwarder sits on a live local socket for the
        full DIAL_BACK_TIMEOUT_S. For serial that window is actively harmful —
        `dhands serial send` writes into that socket, reports success, and the
        bytes are discarded when the forwarder finally gives up (issue #4).

        Bound to the connection the open_channel went out on: a channel_error
        from any other signaling connection is ignored (sequences.md §5).
        """
        pending = self._pending.get(cid)
        if pending is None or pending.fut.done():
            return False
        if pending.owner is not None and pending.owner is not owner:
            return False
        pending.fut.set_exception(
            ChannelOpenError(reason or "bench reported channel open failure", code)
        )
        return True

    def fail_owned(self, owner: object, reason: str) -> int:
        """Fail every pending open that went out on `owner`'s signaling
        connection — it just ended, so no dial-back or channel_error for them
        can come. Returns how many waiters were woken."""
        woken = 0
        for pending in list(self._pending.values()):
            if pending.owner is owner and not pending.fut.done():
                pending.fut.set_exception(ChannelOpenError(reason))
                woken += 1
        return woken

    def discard_pending(self, cid: str) -> None:
        self._pending.pop(cid, None)

    @property
    def pending_count(self) -> int:
        return len(self._pending)


_REGISTRY: AgentRegistry | None = None


def get_agent_registry() -> AgentRegistry:
    """Return the process-wide AgentRegistry, lazily created."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = AgentRegistry()
    return _REGISTRY


def reset_agent_registry() -> None:
    """Drop the singleton — tests start with a fresh registry."""
    global _REGISTRY
    _REGISTRY = None
