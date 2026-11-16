"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import ClassVar

import pytest

import devicehands.infra.workspace as workspace_mod
from devicehands.infra.event_bus import reset_bus
from devicehands.infra.metric_store import reset_metric_store
from devicehands.remote.forwarder import reset_forwarders
from devicehands.remote.registry import reset_agent_registry

# ─── keep every test out of the real workspace ─────────────────────
#
# workspace_root() falls back to config.toml's [workspace] root, then to the
# per-user data directory, when DHANDS_WORKSPACE is unset
# (src/devicehands/infra/workspace.py). A test that forgot to set it used to
# write flash job records, chat sessions and events.jsonl next to — and
# indistinguishable from — records of real board work.


class _DefaultWorkspaceTripwire:
    """Stands in for the two fallback lookups of ``workspace_root()``.

    Each call is recorded and answered harmlessly (no configured root; a
    scratch dir as the default), so a test that reaches the fallback fails
    (see `_isolate_workspace`) instead of reading the real config.toml or
    writing into the real workspace. Tests of the fallback itself put the
    real functions back from ``real_configured_root`` / ``real_default_dir``.
    """

    hits: ClassVar[list[str]] = []
    scratch: ClassVar[Path] = Path("/nonexistent")
    real_configured_root: ClassVar[Callable[[], Path | None]] = workspace_mod._configured_root
    real_default_dir: ClassVar[Callable[[], Path]] = workspace_mod.default_workspace_dir

    @classmethod
    def configured_root(cls) -> Path | None:
        cls.hits.append("config.toml [workspace] root")
        return None

    @classmethod
    def default_dir(cls) -> Path:
        cls.hits.append("user data dir")
        return cls.scratch


@pytest.fixture(scope="session")
def _workspace_tripwire(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    _DefaultWorkspaceTripwire.scratch = tmp_path_factory.mktemp("default-workspace-fallback")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(workspace_mod, "_configured_root", _DefaultWorkspaceTripwire.configured_root)
        mp.setattr(workspace_mod, "default_workspace_dir", _DefaultWorkspaceTripwire.default_dir)
        yield


@pytest.fixture
def workspace_tripwire() -> type[_DefaultWorkspaceTripwire]:
    """The tripwire class, for tests of workspace_root()'s fallback."""
    return _DefaultWorkspaceTripwire


@pytest.fixture(autouse=True)
def _no_adb_path_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's own DHANDS_ADB_PATH would replace every test's adb
    ``bin_path`` (see ``load_config``); tests that want it set it."""
    monkeypatch.delenv("DHANDS_ADB_PATH", raising=False)


@pytest.fixture(autouse=True)
def _isolate_workspace(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    _workspace_tripwire: None,
) -> Iterator[None]:
    """Give every test its own empty DHANDS_WORKSPACE, and fail a test that
    still resolves the default one (e.g. after ``delenv("DHANDS_WORKSPACE")``).

    The dir comes from tmp_path_factory, not ``tmp_path``, so tests that
    inspect their own ``tmp_path`` don't see workspace files in it. A test
    that needs its own workspace keeps doing ``setenv("DHANDS_WORKSPACE", …)``.
    """
    monkeypatch.setenv("DHANDS_WORKSPACE", str(tmp_path_factory.mktemp("dhands-workspace")))
    _DefaultWorkspaceTripwire.hits.clear()
    yield
    hits = list(_DefaultWorkspaceTripwire.hits)
    _DefaultWorkspaceTripwire.hits.clear()
    if hits:
        pytest.fail(
            "workspace_root() fell back to the real default workspace "
            f"({', '.join(hits)}): DHANDS_WORKSPACE was unset during this test. "
            "Point it at a tmp dir instead of deleting it.",
            pytrace=False,
        )


@pytest.fixture
def default_workspace_hits() -> list[str]:
    """The tripwire's record, for tests of the tripwire itself. Clear it
    before returning, or the test fails at teardown."""
    return _DefaultWorkspaceTripwire.hits


@pytest.fixture(autouse=True)
def _reset_event_infra():
    """Reset the event-bus + metric-store + agent-registry + adb-forwarder
    singletons around every test (ADR-049 / ADR-051).

    Paired + ordered (store first, bus second) so the MetricStore never
    holds a reference to a stale bus, and a per-test `create_app()`
    lifespan can't leak listeners across tests (which would double-count
    every `tps_sample`). The agent registry + forwarder are reset too so a
    remote-agent test never sees a connection / pending channel / bound
    listener left by another test. Supersedes the manual `reset_bus()` calls
    scattered across individual test files — those remain harmless."""
    reset_metric_store()
    reset_bus()
    reset_agent_registry()
    reset_forwarders()
    yield
    reset_metric_store()
    reset_bus()
    reset_agent_registry()
    reset_forwarders()


@pytest.fixture
def dummy_transport():
    """Placeholder for a mocked Transport. Real fixture lands in M1 tests."""
    return None
