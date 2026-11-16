"""File and task names the hub and the bench program must agree on.

The bench program (bench-py/) is a standalone script, so every name the two
sides share is written twice. A name changed on one side only breaks the
remote update or the remote start, and only on a real bench host. These
tests read both copies and compare them.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_BENCH = Path(__file__).resolve().parents[2] / "bench-py"


def _constants(path: Path) -> dict[str, object]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: dict[str, object] = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
        ):
            out[node.targets[0].id] = node.value.value
    return out


def test_the_bench_folder_has_the_renamed_files() -> None:
    names = {p.name for p in _BENCH.iterdir() if p.is_file()}
    assert {"dhands_bench.py", "run-bench.bat", "bench.conf.example"} <= names
    # No second program, launcher or template left over from before the rename.
    assert {n for n in names if n.endswith(".py")} == {"dhands_bench.py", "setup_remote_start.py"}
    assert {n for n in names if n.endswith(".bat")} == {"run-bench.bat", "setup-remote-start.bat"}
    assert {n for n in names if n.endswith(".example")} == {"bench.conf.example"}


def test_hub_and_bench_agree_on_bundle_names() -> None:
    from devicehands.remote import agent_update

    bench = _constants(_BENCH / "dhands_bench.py")
    assert agent_update.bundle_dir() == _BENCH
    assert agent_update.BUNDLE_MAIN == bench["_BUNDLE_MAIN"] == "dhands_bench.py"
    assert agent_update.BUNDLE_CONFIG == bench["_BUNDLE_CONFIG"] == bench["DEFAULT_CONFIG_NAME"] == "bench.conf"
    assert (_BENCH / agent_update.BUNDLE_MAIN).is_file()
    assert bench["_STATE_DIR_NAME"] == ".dhands-bench"


def test_launcher_runs_the_main_file_and_sets_the_launcher_variable() -> None:
    bench = _constants(_BENCH / "dhands_bench.py")
    bat = (_BENCH / bench["_LAUNCHER_BAT"]).read_text(encoding="utf-8")  # type: ignore[operator]
    assert f'set "{bench["_LAUNCHER_ENV"]}={bench["_LAUNCHER_BAT"]}"' in bat
    assert re.search(rf"%PY% {re.escape(str(bench['_BUNDLE_MAIN']))} %\*", bat)
    assert f"if not exist {bench['_BUNDLE_CONFIG']} (" in bat
    assert "DHANDS_BENCH_NO_PAUSE" in bat


def test_hub_starts_the_task_the_bench_registers() -> None:
    from devicehands.remote import agent_start

    setup = _constants(_BENCH / "setup_remote_start.py")
    assert agent_start.DEFAULT_TASK == setup["TASK_NAME"] == "dhands-bench"
    # The hub tails the log file the shipped config template writes.
    template = (_BENCH / "bench.conf.example").read_text(encoding="utf-8")
    assert re.search(r"^#?log_file=logs/bench\.log$", template, re.M)
    assert "logs\\bench.log" in agent_start._TAIL
