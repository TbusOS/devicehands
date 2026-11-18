"""The agent's partition allowlist reaches the UI (ADR-056 follow-up).

Real-hardware defect, 2026-08-10: the web page's partition picker was a
hard-coded list of four names. The bench's agent allowed exactly one, and it
was not among them — so every flash started from the web ended at
FLASH_PARTITION_REJECTED while the same flash from the CLI worked. The
plumbing was fine end to end; only the list of choices was invented by the
side that could not know.

The subtle half, and what most of these tests pin: **empty means "no allowlist
configured", i.e. any well-formed name — not "nothing allowed"**. A consumer
that renders an empty picker on an empty list turns a permissive bench into an
unusable one.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from devicehands.remote.registry import AgentConnection, AgentRegistry

_AGENT_PATH = Path(__file__).resolve().parents[2] / "bench-py" / "dhands_bench.py"


def _load_agent():
    spec = importlib.util.spec_from_file_location("dhands_bench_partitions_under_test", _AGENT_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


agent_mod = _load_agent()


async def _noop(_m):  # pragma: no cover - registry needs a send callable
    return None


def _conn(**kw) -> AgentConnection:
    defaults = dict(agent_id="a", name="bench", send_control=_noop, registry=AgentRegistry())
    defaults.update(kw)
    return AgentConnection(**defaults)  # type: ignore[arg-type]


def _bench(partitions: list[str]) -> SimpleNamespace:
    """An agent object carrying a list, as the flash service reads one.
    No v2 bench sends a list (the field left the hello with protocol v2);
    what these pin is how the service and the web view carry whatever list
    an agent object holds — empty included."""
    return SimpleNamespace(
        agent_id="a", name="bench", caps=["adb", "fastboot"], flash_partitions=partitions
    )


# ── the agent side: the rule must leave the machine that owns it ──
# Protocol v2 (DESIGN.md 3.6 / 3.7): no list rides the hello; the partition
# rule rides the wire as the flash op's parameter rule, which the hub and
# the web page read from the op table.


def _partition_rules(monkeypatch) -> list[dict]:
    monkeypatch.setattr(agent_mod, "_OPS", agent_mod._build_ops("/opt/example/fastboot", {}))
    hello = json.loads(agent_mod._hello("a", "n"))
    assert "flash_partitions" not in hello
    rules = [p for op in hello["ops"] for p in op["params"] if p["kind"] == "partition"]
    assert rules, "no partition parameter in the table: these checks would be vacuous"
    return rules


def test_hello_reports_no_list_even_with_one_left_in_agent_conf(monkeypatch):
    """ADR-070: no list limits partition names any more, so none is offered
    — a picker built from a leftover line would hide partitions the agent
    accepts. The rule on the wire is the shape rule, not the leftover list."""
    monkeypatch.setattr(agent_mod, "_FLASH_PARTITIONS", frozenset({"cfg", "boot"}))
    for rule in _partition_rules(monkeypatch):
        assert rule["pattern"] == agent_mod._PARTITION_PATTERN
        assert agent_mod._rule_fullmatch(rule["pattern"], "vendor_x")


def test_every_partition_parameter_carries_the_rule(monkeypatch):
    """The hub checks names before it opens a channel, with the rule the
    bench sent: every partition parameter has one."""
    monkeypatch.setattr(agent_mod, "_FLASH_PARTITIONS", frozenset())
    for rule in _partition_rules(monkeypatch):
        assert rule["pattern"] and rule["max_len"] >= 1


@pytest.mark.parametrize("name", ["cfg", "boot", "vendor_x", "boot_a", "-w", "a/b", "a b", ""])
def test_allowlist_and_hello_cannot_drift(monkeypatch, name):
    """Whatever the agent advertises, it must actually accept — otherwise the
    picker offers a name the very same process will refuse — and what it
    refuses, the advertised rule must refuse too."""
    monkeypatch.setattr(agent_mod, "_FLASH_PARTITIONS", frozenset({"cfg"}))
    (rule, *_) = _partition_rules(monkeypatch)
    advertised = len(name) <= rule["max_len"] and agent_mod._rule_fullmatch(rule["pattern"], name)
    assert advertised == agent_mod._partition_allowed(name)


# ── the hub side: carry it, don't invent it ────────────────────────


def test_a_v2_connection_has_no_list() -> None:
    """Protocol v2 carries no partition list: a connection's is always empty
    (= any well-formed name), never a crash or a fabricated list."""
    assert _conn().flash_partitions == []


def test_status_listing_still_has_the_key() -> None:
    """The status page reads the key; it stays, empty."""
    reg = AgentRegistry()
    conn = _conn(registry=reg)
    reg._agents[conn.agent_id] = conn  # bypass the async register for a pure read
    assert reg.list_agents()[0]["flash_partitions"] == []


def test_flash_view_reports_the_agents_list(monkeypatch):
    from devicehands.remote import forwarder

    conn = _bench(["cfg"])
    monkeypatch.setattr(
        "devicehands.remote.registry.get_agent_registry",
        lambda: type("R", (), {"current_agent": lambda _s: conn})(),
    )
    view = forwarder._flash_view()
    assert view["partitions"] == ["cfg"]
    assert view["available"] is True


def test_flash_view_with_no_agent_is_empty_not_a_crash(monkeypatch):
    from devicehands.remote import forwarder

    monkeypatch.setattr(
        "devicehands.remote.registry.get_agent_registry",
        lambda: type("R", (), {"current_agent": lambda _s: None})(),
    )
    view = forwarder._flash_view()
    assert view["partitions"] == []
    assert view["available"] is False


def test_service_status_carries_partitions():
    from devicehands.remote.flash import FlashService

    conn = _bench(["cfg", "boot"])
    svc = FlashService(lambda: conn)
    assert svc.status()["partitions"] == ["cfg", "boot"]


def test_service_partitions_empty_without_an_agent():
    from devicehands.remote.flash import FlashService

    svc = FlashService(lambda: None)
    assert svc.partitions() == []


@pytest.mark.parametrize("advertised", [[], ["cfg"], ["cfg", "boot"]])
def test_status_always_has_the_key(advertised):
    """A consumer must never have to distinguish "absent" from "empty" —
    that ambiguity is what a fallback rule cannot be written against."""
    from devicehands.remote.flash import FlashService

    svc = FlashService(lambda: _bench(advertised))
    assert "partitions" in svc.status()
