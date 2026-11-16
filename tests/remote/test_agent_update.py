"""Agent side of remote update and single instance (ADR-060), piece by piece.

The whole flow with real processes is in test_agent_update_e2e.py; this file
pins the rules each piece has to keep on its own: what a bundle may contain,
which process counts as "the same agent", and when a running agent may be
killed rather than asked.
"""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

from devicehands.remote import agent_update as hub_side

_AGENT_PATH = Path(__file__).resolve().parents[2] / "bench-py" / "dhands_bench.py"


def _load_agent():
    spec = importlib.util.spec_from_file_location("dhands_bench_update_under_test", _AGENT_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


agent = _load_agent()


def _zip(entries: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return out.getvalue()


# ── the bundle ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name",
    [
        "dhands_bench.py",
        "run-bench.bat",
        "README.md",
        "requirements.txt",
        "bench.conf.example",
        "bench.conf",
        "FASTBOOT-SETUP.md",
    ],
)
def test_every_file_the_folder_ships_is_allowed(name):
    assert agent._bundle_member_ok(name)


@pytest.mark.parametrize(
    "name",
    [
        "../dhands_bench.py",
        "sub/dhands_bench.py",
        "sub\\x.py",
        ".hidden.py",
        "C:x.py",
        "tool.exe",
        "lib.dll",
        "",
        "-x.py",
        "a" * 70 + ".py",
    ],
)
def test_anything_that_could_land_elsewhere_or_run_differently_is_refused(name):
    assert not agent._bundle_member_ok(name)


def test_the_hub_packs_by_the_same_rules_the_agent_unpacks_by():
    """The two sides are separate programs; a file one packs and the other
    refuses fails every update."""
    assert hub_side.BUNDLE_NAME_RE.pattern == agent._BUNDLE_NAME_RE.pattern
    assert hub_side.BUNDLE_SUFFIXES == agent._BUNDLE_SUFFIXES
    assert hub_side.BUNDLE_CONFIG == agent._BUNDLE_CONFIG
    assert hub_side.BUNDLE_MAIN == agent._BUNDLE_MAIN
    assert hub_side.build_of(_AGENT_PATH.read_bytes()) == agent._file_build(_AGENT_PATH)


def _unpack(tmp_path: Path, entries: dict[str, bytes]) -> list[str]:
    bundle = tmp_path / "b.zip"
    bundle.write_bytes(_zip(entries))
    return agent._unpack_bundle(bundle, tmp_path / "staged")


def test_unpack_empties_the_staging_folder_first(tmp_path):
    (tmp_path / "staged").mkdir()
    (tmp_path / "staged" / "stale.py").write_text("old")
    names = _unpack(tmp_path, {"dhands_bench.py": b"x", "README.md": b"y"})
    assert names == ["README.md", "dhands_bench.py"]
    assert sorted(p.name for p in (tmp_path / "staged").iterdir()) == names


@pytest.mark.parametrize(
    ("entries", "why"),
    [
        ({"dhands_bench.py": b"x", "../evil.py": b"y"}, "not an allowed file name"),
        ({"README.md": b"y"}, "no dhands_bench.py"),
        ({"dhands_bench.py": b"x", "DHANDS_BENCH.py": b"y"}, "appears twice"),
    ],
)
def test_a_bad_bundle_is_refused_whole(tmp_path, entries, why):
    with pytest.raises(agent._JobRejected) as e:
        _unpack(tmp_path, entries)
    assert e.value.code == "AGENT_UPDATE_REJECTED" and why in e.value.message
    assert "nothing was changed" in e.value.message
    assert not (tmp_path / "staged").exists(), "refused before anything was written"


def test_an_oversized_bundle_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "_BUNDLE_MAX_BYTES", 10)
    with pytest.raises(agent._JobRejected):
        _unpack(tmp_path, {"dhands_bench.py": b"x" * 11})


def test_not_a_zip_is_refused(tmp_path):
    bundle = tmp_path / "b.zip"
    bundle.write_bytes(b"not a zip")
    with pytest.raises(agent._JobRejected) as e:
        agent._unpack_bundle(bundle, tmp_path / "staged")
    assert e.value.code == "AGENT_UPDATE_REJECTED"


def test_changed_files_compares_contents_and_the_config_target(tmp_path):
    staged, install = tmp_path / "s", tmp_path / "i"
    staged.mkdir()
    install.mkdir()
    for name, new, old in (("a.py", b"1", b"1"), ("b.py", b"2", b"x")):
        (staged / name).write_bytes(new)
        (install / name).write_bytes(old)
    (staged / "c.md").write_bytes(b"new file")
    (staged / "bench.conf").write_bytes(b"hub_url=x\n")
    conf = tmp_path / "elsewhere.conf"
    conf.write_bytes(b"hub_url=y\n")
    assert agent._changed_files(staged, install, ["a.py", "b.py", "c.md"], None) == ["b.py", "c.md"]
    assert agent._changed_files(staged, install, ["a.py"], conf) == ["bench.conf"]


# ── restart arguments ────────────────────────────────────────────────


def test_without_option_drops_both_spellings():
    argv = ["--config", "a.conf", "-v", "--config=b.conf", "--name", "x"]
    assert agent._without_option(argv, "--config") == ["-v", "--name", "x"]


def test_restart_keeps_the_arguments_with_an_absolute_config(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(agent, "_LAUNCH_ARGV", ["--config", "rel.conf", "-v"])
    monkeypatch.setattr(agent, "_CONFIG_USED", "rel.conf")
    monkeypatch.setattr(agent, "_CONFIG_EXPLICIT", True)
    assert agent._relaunch_argv() == ["-v", "--config", str(tmp_path / "rel.conf")]
    # the default config (bench.conf next to the script) is found again on
    # its own — naming it would be redundant
    monkeypatch.setattr(agent, "_LAUNCH_ARGV", ["-v"])
    monkeypatch.setattr(agent, "_CONFIG_EXPLICIT", False)
    assert agent._relaunch_argv() == ["-v"]


def test_restart_goes_through_run_agent_bat_only_on_windows_when_it_started_there(
    monkeypatch, tmp_path
):
    (tmp_path / "run-bench.bat").write_text("@echo off")
    monkeypatch.setenv("DHANDS_BENCH_LAUNCHER", "run-bench.bat")
    monkeypatch.setattr(agent.sys, "platform", "win32")
    assert agent._relaunch_mode(tmp_path) == "bat"
    monkeypatch.delenv("DHANDS_BENCH_LAUNCHER")
    assert agent._relaunch_mode(tmp_path) == "python"
    monkeypatch.setattr(agent.sys, "platform", "linux")
    monkeypatch.setenv("DHANDS_BENCH_LAUNCHER", "run-bench.bat")
    assert agent._relaunch_mode(tmp_path) == "python"


# ── which process is "the same agent" ────────────────────────────────


def test_one_config_file_is_one_agent(tmp_path):
    a, b = tmp_path / "a" / "bench.conf", tmp_path / "b" / "bench.conf"
    assert agent._instance_key(str(a), "id1") == agent._instance_key(str(a), "id2")
    assert agent._instance_key(str(a), "id1") != agent._instance_key(str(b), "id1")
    assert agent._instance_key("", "id1") != agent._instance_key("", "id2")


def _started(argv: list[str]) -> subprocess.Popen[bytes]:
    """Start `argv` and return once its command line can be read.

    Popen returns when the child has begun exec(), not when exec() is done:
    for a short while after that /proc/<pid>/cmdline is still empty
    (measured on Linux: empty in about 1 of 200 reads on 2 CPUs, more than
    half of them on 64). _same_process takes an empty command line for "not
    an agent", so _stop_instance then reported the process gone without
    stopping it — the CI failure of the grace test. A running agent has long been
    through exec(), so only a test that asks right after Popen can hit it."""
    p = subprocess.Popen(argv)
    deadline = time.monotonic() + 10
    while not agent._posix_cmdline(p.pid).strip():
        if time.monotonic() > deadline:
            p.kill()
            raise AssertionError(f"pid {p.pid}: no command line after 10s")
        time.sleep(0.01)
    return p


def _child(code: str) -> subprocess.Popen[bytes]:
    """A stand-in agent: its command line names dhands_bench.py, which is what
    _same_process checks on POSIX."""
    return _started([sys.executable, "-c", code, "dhands_bench.py"])


def _rec(p: subprocess.Popen[bytes]) -> dict:
    return {"pid": p.pid, "created": time.time()}


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX identity check")
def test_a_pid_whose_command_line_is_not_an_agent_is_not_the_agent():
    p = _started([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert not agent._same_process({"pid": p.pid})
    finally:
        p.kill()
        p.wait()
    assert not agent._same_process({"pid": p.pid})
    assert not agent._same_process({"pid": "garbage"})


_COOPERATIVE = """
import sys, time, pathlib
flag = pathlib.Path(sys.argv[2])
while not flag.exists():
    time.sleep(0.05)
sys.exit(75)
"""


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX processes")
def test_an_agent_that_honours_the_request_is_not_killed(tmp_path):
    flag_holder: list[Path] = []
    p = _started(
        [sys.executable, "-c", _COOPERATIVE, "dhands_bench.py", str(tmp_path / "pending")]
    )
    try:
        # the stand-in watches the flag path for ITS pid; point it there
        flag = agent._stop_flag(tmp_path, p.pid)
        flag_holder.append(flag)
        (tmp_path / "pending").symlink_to(flag)
        assert agent._stop_instance(tmp_path, _rec(p), lambda _m: None, grace_s=10.0)
        assert p.wait(timeout=5) == 75
    finally:
        p.kill()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX processes")
def test_an_agent_that_ignores_the_request_is_killed_after_the_grace(tmp_path):
    p = _child("import time; time.sleep(60)")
    said: list[str] = []
    try:
        t0 = time.monotonic()
        assert agent._stop_instance(tmp_path, _rec(p), said.append, grace_s=0.5)
        assert time.monotonic() - t0 < 10
        assert p.wait(timeout=5) != 0
        assert any("killing it" in m for m in said)
    finally:
        p.kill()


_BUSY = """
import sys, time, pathlib
busy = pathlib.Path(sys.argv[2])
busy.touch()
time.sleep(2.0)
busy.unlink()
sys.exit(75)
"""


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX processes")
def test_an_agent_in_the_middle_of_a_job_is_waited_for_not_killed(tmp_path):
    """A flash in progress outlasts any grace period; killing fastboot then
    leaves a half-written partition."""
    p = _started(
        [sys.executable, "-c", _BUSY, "dhands_bench.py", str(tmp_path / "busy-marker")]
    )
    try:
        busy = agent._stop_flag(tmp_path, p.pid).with_name(f"stop-{p.pid}.busy")
        (tmp_path / "busy-marker").symlink_to(busy)
        assert agent._stop_instance(tmp_path, _rec(p), lambda _m: None, grace_s=0.3)
        assert p.wait(timeout=5) == 75, "it finished its job and left on its own"
    finally:
        p.kill()


# ── the op refuses what it cannot finish ─────────────────────────────


async def test_no_record_means_no_update(monkeypatch, tmp_path):
    monkeypatch.setattr(agent, "_INSTANCE_PATH", None)
    with pytest.raises(agent._JobRejected) as e:
        await agent._op_agent_update(None, {"image": str(tmp_path / "b.zip")})
    assert e.value.code == "AGENT_UPDATE_REJECTED"


async def test_no_fixed_identity_means_no_update(monkeypatch, tmp_path):
    """Without a config file or --agent-id the restarted copy gets a new
    random id; the helper could never see it come back and would roll a
    good build back."""
    monkeypatch.setattr(agent, "_INSTANCE_PATH", tmp_path / "instance-x.json")
    monkeypatch.setattr(agent, "_IDENTITY_STABLE", False)
    with pytest.raises(agent._JobRejected) as e:
        await agent._op_agent_update(None, {"image": str(tmp_path / "b.zip")})
    assert "no fixed identity" in e.value.message


def test_the_op_is_in_every_table_and_takes_the_job_lock():
    ops = agent._build_ops("", {})
    row = ops["agent-update"]
    assert row.image and row.builtin is not None and not row.argv
    assert row.exclusive, "must never land in the middle of a flash"
    assert row.mutates and row.max_image_bytes == agent._BUNDLE_MAX_BYTES


def test_a_param_may_not_take_the_reserved_image_name():
    with pytest.raises(ValueError, match="reserved"):
        agent.OpSpec(
            "x",
            "host",
            "s",
            params=(agent.ParamSpec("image", "int", min=0, max=1),),
            builtin=agent._op_agent_update,
        )


# ── forced stop and unplanned failures (review of b883a6e) ────────────


def test_a_forced_stop_spares_the_caller_and_everything_it_started():
    """The update helper is a child of the agent it stops; `taskkill /T`
    would have killed it mid-update. pid -> (parent, exe)."""
    table = {
        10: (1, "cmd.exe"),  # the agent's window
        11: (10, "python.exe"),  # the agent
        12: (11, "fastboot.exe"),  # a job the agent runs
        13: (11, "python.exe"),  # the update helper (the caller)
        14: (13, "cmd.exe"),  # a new agent the helper started
        20: (1, "explorer.exe"),  # unrelated
    }
    assert agent._tree_to_kill(table, 11, spare=13) == [12, 11]
    assert agent._tree_to_kill(table, 10, spare=13) == [12, 11, 10]
    assert agent._tree_to_kill({5: (5, "x")}, 5, spare=0) == [5], "a self-parent loop ends"


def _plan(tmp_path: Path) -> Path:
    install = tmp_path / "install"
    (install / ".dhands-bench").mkdir(parents=True)
    plan = {
        "id": "u1",
        "install_dir": str(install),
        "staged_dir": str(install / ".dhands-bench" / "staged"),
        "files": ["dhands_bench.py"],
        "changed": ["dhands_bench.py"],
        "config_target": "",
        "instance_file": str(install / ".dhands-bench" / "instance-x.json"),
        "old": {"pid": 1, "created": 0},
        "from_build": "old",
        "to_build": "new",
        "relaunch": {"mode": "python", "python": sys.executable, "argv": []},
        "health_timeout_s": 1,
        "settle_s": 0,
    }
    path = install / ".dhands-bench" / "update-plan.json"
    path.write_text(json.dumps(plan))
    return path


def test_an_unplanned_failure_after_the_stop_still_restarts_the_old_build(tmp_path, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(agent, "_stop_instance", lambda *a, **k: True)
    monkeypatch.setattr(agent.time, "sleep", lambda _s: None)
    monkeypatch.setattr(agent, "_backup_files", lambda *a: {"files": {}, "config": None})

    def boom(*_a):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(agent, "_install_files", boom)
    monkeypatch.setattr(agent, "_restore_files", lambda *a: calls.append("restore"))
    monkeypatch.setattr(
        agent, "_launch_and_wait", lambda _p, build, _say, **_k: calls.append(f"start {build}")
    )
    plan = _plan(tmp_path)
    assert agent._apply_update_main(str(plan)) == 3
    assert calls == ["restore", "start old"]
    result = agent._read_json(plan.parent / "update-result.json")
    assert result["outcome"] == "rolled_back"
    assert "update helper crashed" in (plan.parent / "update.log").read_text()


def test_a_malformed_plan_fails_before_the_running_agent_is_stopped(tmp_path, monkeypatch):
    stopped: list[object] = []
    monkeypatch.setattr(agent, "_stop_instance", lambda *a, **k: stopped.append(a) or True)
    plan = _plan(tmp_path)
    data = agent._read_json(plan)
    del data["relaunch"]
    plan.write_text(json.dumps(data))
    assert agent._apply_update_main(str(plan)) == 3
    assert stopped == [], "the old agent must still be running"


def test_no_start_time_means_no_record(tmp_path, monkeypatch):
    """A record without a creation time never matches: the next start would
    not stop this instance and an update could never see it come back."""
    import argparse

    monkeypatch.setattr(agent, "_install_dir", lambda: tmp_path)
    monkeypatch.setattr(agent, "_own_created", lambda: 0.0)
    monkeypatch.setattr(agent, "_INSTANCE_PATH", None)
    agent._claim_instance(argparse.Namespace(config_used="", agent_id="a"))
    assert agent._INSTANCE_PATH is None
    assert list((tmp_path / ".dhands-bench").glob("instance-*.json")) == []


# ── a start that never reached the hub leaves an account (10-20 bench) ──


def test_the_tree_is_listed_parents_first_with_age_and_command_line():
    table = {
        10: (1, "cmd.exe"),  # the window the helper started
        11: (10, "conhost.exe"),
        12: (10, "python.exe"),  # run-bench.bat's `python -c` check
        13: (12, "adb.exe"),
        14: (10, "python.exe"),  # recycled pid: older than its "parent"
        20: (1, "explorer.exe"),
    }
    created = {10: 100.0, 11: 100.1, 12: 101.0, 13: 101.5, 14: 50.0}
    cmd = {
        10: 'cmd.exe /c "run-bench.bat"',
        12: "python  -c 'print(\"  [v]\")'\n",
        13: "adb devices",
    }
    lines = agent._describe_tree(table, 10, created.get, lambda p: cmd.get(p, ""), now=160.0)
    assert lines == [
        'pid 10 cmd.exe, 60s old: cmd.exe /c "run-bench.bat"',
        "  pid 11 conhost.exe, 60s old: (command line unreadable)",
        "  pid 12 python.exe, 59s old: python  -c 'print(\"  [v]\")'",
        "    pid 13 adb.exe, 58s old: adb devices",
    ]
    assert agent._describe_tree(table, 99, created.get, cmd.get, now=0.0) == [], "root gone"


def test_a_report_step_that_hangs_or_fails_does_not_hold_up_the_rollback():
    t0 = time.monotonic()
    out = agent._within(0.3, lambda: time.sleep(30))
    assert isinstance(out, TimeoutError) and time.monotonic() - t0 < 5
    assert isinstance(agent._within(5, lambda: 1 / 0), ZeroDivisionError)
    assert agent._within(5, lambda: "ok") == "ok"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")
def test_a_stalled_start_is_written_down_while_it_is_still_running(tmp_path):
    """What 10-20 lacked: which processes were still up under the start, and
    whether the new build ever recorded itself."""
    marker = "stalled-start-marker"
    proc = subprocess.Popen(
        ["sh", "-c", f'{sys.executable} -c "import time; time.sleep(30)" {marker}; true']
    )
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not any(
            marker in agent._posix_cmdline(pid) and pid != proc.pid
            for pid, (parent, _n) in agent._process_table().items()
            if parent == proc.pid
        ):
            time.sleep(0.05)
        instance = tmp_path / "instance-x.json"
        instance.write_text(json.dumps({"build": "old", "pid": 1, "created": 0}))
        said: list[str] = []
        agent._record_stalled_start(proc, instance, "new", said.append)
    finally:
        proc.kill()
        proc.wait()
    text = "\n".join(said)
    assert "build old's — build new never wrote its own" in text, text
    assert f"processes under the start (pid {proc.pid}):" in text, text
    child = [s for s in said if s.startswith("    pid ") and marker in s]
    assert child and "time.sleep(30)" in child[0], text


def test_nothing_left_and_an_unreadable_table_are_said_too(tmp_path, monkeypatch):
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    said: list[str] = []
    agent._record_stalled_start(proc, tmp_path / "none.json", "new", said.append)
    assert any(s.endswith(": none left") for s in said), said
    assert said[0].startswith("instance record: none — build new either never"), said

    def broken() -> dict:
        raise PermissionError("denied")

    monkeypatch.setattr(agent, "_process_table", broken)
    said.clear()
    agent._record_stalled_start(proc, tmp_path / "none.json", "new", said.append)
    assert any("not read — denied" in s for s in said), said
