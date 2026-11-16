"""Check that every entry-point module of the package can be imported.

CI runs this on Ubuntu, macOS and Windows. The modules come from
``[project.scripts]`` in pyproject.toml, so a new or renamed entry point
is picked up without editing this file.

Each module is imported in its own subprocess. A module that fails to
import (for example a top-level ``import fcntl`` on Windows) does not
hide the result of the next one, and modules already imported by one
check cannot make another check pass by accident.

Some failures are known and planned for a later phase. They are listed
in ``KNOWN_FAILURES``. The rules:

* a failure that is not in the table          -> exit 1
* a table entry whose import now succeeds     -> exit 1 (remove the entry)
* a table entry naming a module that is not an
  entry point any more (renamed or removed)   -> exit 1 (fix the entry)
* everything else                             -> exit 0

The second and third rules keep the table from going stale.

Run:
    uv run python scripts/ci_import_check.py
    uv run python scripts/ci_import_check.py --self-test

Standard library only, so it also runs before the project is installed
(it then reports every module as failed, which is the correct answer).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PYPROJECT = REPO_ROOT / "pyproject.toml"
IMPORT_TIMEOUT_S = 120

# (system, module) -> (reason, phase that plans the fix).
# system is one of: "linux", "macos", "windows" (see current_system()).
# Phases are the ones in docs/devicehands-design.md section 9.
KNOWN_FAILURES: dict[tuple[str, str], tuple[str, str]] = {
    ("windows", "alb.api.server"): (
        "alb.api.server -> alb.api.terminal_route -> alb.transport.interactive, "
        "which imports fcntl and termios at module level (POSIX only)",
        "P1",
    ),
}


@dataclass(frozen=True)
class ImportResult:
    module: str
    ok: bool
    detail: str  # last line of the error when ok is False, else ""


@dataclass(frozen=True)
class Verdict:
    exit_code: int
    lines: list[str]


def current_system() -> str:
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform in ("win32", "cygwin"):
        return "windows"
    return sys.platform


def entry_modules(pyproject: Path) -> list[str]:
    """Return the module part of each [project.scripts] value, in file order."""
    with pyproject.open("rb") as f:
        data = tomllib.load(f)
    scripts = data.get("project", {}).get("scripts", {})
    if not scripts:
        raise SystemExit(f"error: no [project.scripts] table in {pyproject}")
    modules: list[str] = []
    for name, target in scripts.items():
        module = str(target).split(":", 1)[0].strip()
        if not module:
            raise SystemExit(f"error: entry point {name!r} has no module: {target!r}")
        if module not in modules:
            modules.append(module)
    return modules


def _last_line(text: str) -> str:
    for line in reversed(text.splitlines()):
        if line.strip():
            return line.strip()
    return ""


def import_in_subprocess(module: str) -> ImportResult:
    # -I: ignore PYTHONPATH and the current directory, so the import resolves
    # against the installed package only, as the real entry point would.
    code = "import importlib, sys; importlib.import_module(sys.argv[1])"
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-c", code, module],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=IMPORT_TIMEOUT_S,
            env=env,
            cwd=REPO_ROOT,
        )
    except subprocess.TimeoutExpired:
        return ImportResult(module, False, f"import timed out after {IMPORT_TIMEOUT_S}s")
    if proc.returncode == 0:
        return ImportResult(module, True, "")
    detail = _last_line(proc.stderr) or _last_line(proc.stdout)
    return ImportResult(module, False, detail or f"exit code {proc.returncode}, no output")


def judge(
    system: str,
    modules: list[str],
    results: list[ImportResult],
    known: dict[tuple[str, str], tuple[str, str]],
) -> Verdict:
    """Apply the rules in the module docstring. Pure function, no I/O."""
    lines: list[str] = []
    bad = 0
    for r in results:
        entry = known.get((system, r.module))
        if r.ok and entry is None:
            lines.append(f"PASS     {r.module}")
        elif r.ok and entry is not None:
            bad += 1
            lines.append(
                f"FIXED    {r.module}: listed in KNOWN_FAILURES for {system} but imports "
                f"fine now; it is already fixed, remove the entry from the table"
            )
        elif entry is not None:
            reason, phase = entry
            lines.append(f"KNOWN    {r.module}: {r.detail}")
            lines.append(f"         expected: {reason} (fix planned in {phase})")
        else:
            bad += 1
            lines.append(f"FAIL     {r.module}: {r.detail}")
    for sys_name, module in sorted(known):
        if sys_name == system and module not in modules:
            bad += 1
            lines.append(
                f"STALE    {module}: listed in KNOWN_FAILURES for {system} but it is not "
                f"an entry point in [project.scripts]; update or remove the entry"
            )
    status = "OK" if bad == 0 else f"{bad} problem(s)"
    lines.append(f"result on {system}: {status}")
    return Verdict(0 if bad == 0 else 1, lines)


def run(pyproject: Path) -> int:
    system = current_system()
    modules = entry_modules(pyproject)
    print(f"system: {system}, python: {sys.version.split()[0]}, interpreter: {sys.executable}")
    print(f"entry modules from {pyproject.name} [project.scripts]: {', '.join(modules)}")
    results = [import_in_subprocess(m) for m in modules]
    verdict = judge(system, modules, results, KNOWN_FAILURES)
    for line in verdict.lines:
        print(line)
    return verdict.exit_code


def self_test() -> int:
    """Feed fake results to judge() and check the exit codes."""
    mods = ["pkg.a", "pkg.b"]
    known = {("windows", "pkg.b"): ("needs fcntl", "P1")}
    ok_a = ImportResult("pkg.a", True, "")
    ok_b = ImportResult("pkg.b", True, "")
    bad_a = ImportResult("pkg.a", False, "ModuleNotFoundError: No module named 'x'")
    bad_b = ImportResult("pkg.b", False, "ModuleNotFoundError: No module named 'fcntl'")

    # (name, system, modules, results, table, expected exit code, text that must appear)
    cases = [
        ("all pass, nothing known", "linux", mods, [ok_a, ok_b], known, 0, "result on linux: OK"),
        ("known failure still fails", "windows", mods, [ok_a, bad_b], known, 0, "KNOWN    pkg.b"),
        (
            "entry for another system is ignored",
            "linux",
            mods,
            [ok_a, ok_b],
            known,
            0,
            "PASS     pkg.b",
        ),
        # Deliberately wrong inputs: each one must be judged a failure.
        ("unexpected failure", "linux", mods, [bad_a, ok_b], known, 1, "FAIL     pkg.a"),
        (
            "unexpected failure next to a known one",
            "windows",
            mods,
            [bad_a, bad_b],
            known,
            1,
            "FAIL     pkg.a",
        ),
        ("known entry now passes", "windows", mods, [ok_a, ok_b], known, 1, "remove the entry"),
        (
            "entry names a module that is gone",
            "windows",
            ["pkg.a"],
            [ok_a],
            known,
            1,
            "STALE    pkg.b",
        ),
    ]

    def check(case: tuple) -> bool:
        _name, system, modules, results, table, want, text = case
        v = judge(system, modules, results, table)
        joined = "\n".join(v.lines)
        return v.exit_code == want and text in joined

    failed = 0
    for case in cases:
        good = check(case)
        failed += 0 if good else 1
        print(f"{'ok  ' if good else 'FAIL'} {case[0]} (expect exit {case[5]})")

    # Guard against a harness that passes everything: a case with a wrong
    # expectation must be reported as a mismatch by check().
    wrong = ("harness guard", "linux", mods, [bad_a, ok_b], known, 0, "FAIL     pkg.a")
    if check(wrong):
        failed += 1
        print("FAIL harness guard: a wrong expectation was accepted")
    else:
        print("ok   harness guard: a wrong expectation is rejected")

    print(f"self-test: {len(cases) + 1 - failed}/{len(cases) + 1} passed")
    return 0 if failed == 0 else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--self-test", action="store_true", help="test the judging rules with fake results"
    )
    ap.add_argument(
        "--pyproject", type=Path, default=DEFAULT_PYPROJECT, help="path to pyproject.toml"
    )
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()
    return run(args.pyproject)


if __name__ == "__main__":
    sys.exit(main())
