#!/usr/bin/env python3
"""Check that every ``ADR-<number>`` reference points to an existing ADR entry.

The ADR entries live in ``docs/design-decisions.md``; an entry is a level-2
heading ``## ADR-<number> ...`` outside fenced code blocks. Lower-level
headings such as ``#### ADR-001`` (verbatim copies kept inside another entry)
are not entries.

The script scans every git-tracked text file (``git ls-files``), skipping
``docs/app/``, ``uv.lock`` and binary files, and reports each reference whose
number has no entry, as ``file:line``. Numbers are compared as integers, so
``ADR-7`` and ``ADR-007`` are the same reference. A reserved number that was
never used (for example 042) has no entry, so a reference to it is reported.

Usage:
    python3 scripts/check_adr_refs.py            # check the repository
    python3 scripts/check_adr_refs.py --self-test

Exit codes: 0 = all references resolve, 1 = unresolved references found
(or a self-test case failed), 2 = usage or environment error.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path

DECISIONS_FILE = "docs/design-decisions.md"
EXCLUDED_PREFIXES = ("docs/app/",)
EXCLUDED_FILES = ("uv.lock",)

# "ADR-050", "ADR-7", "adr-050", "ADR_050", and the same with an en dash
# (U+2013) or em dash (U+2014) in place of the hyphen.
# Not matched on purpose: letter IDs such as "ADR-D", and the second number
# of a slash shorthand such as "ADR-050/051".
REF_RE = re.compile(r"(?<![A-Za-z0-9])ADR[-_\u2013\u2014](\d+)(?!\d)", re.IGNORECASE)
ENTRY_RE = re.compile(r"^## ADR-(\d+)\b")
FENCE_RE = re.compile(r"^\s*(```|~~~)")


def parse_entries(text: str) -> set[int]:
    """Return the ADR numbers that have a level-2 entry heading."""
    entries: set[int] = set()
    in_fence = False
    for line in text.splitlines():
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = ENTRY_RE.match(line)
        if m:
            entries.add(int(m.group(1)))
    return entries


def find_refs(text: str) -> Iterable[tuple[int, int, str]]:
    """Yield (line_number, adr_number, matched_text) for each reference."""
    for lineno, line in enumerate(text.splitlines(), start=1):
        for m in REF_RE.finditer(line):
            yield lineno, int(m.group(1)), m.group(0)


def check(files: Mapping[str, str], entries: set[int]) -> list[str]:
    """Return one problem line per unresolved reference."""
    problems: list[str] = []
    for path in sorted(files):
        for lineno, num, raw in find_refs(files[path]):
            if num not in entries:
                problems.append(f"{path}:{lineno}: {raw} has no entry in {DECISIONS_FILE}")
    return problems


def is_excluded(path: str) -> bool:
    return path in EXCLUDED_FILES or path.startswith(EXCLUDED_PREFIXES)


def load_repo(root: Path) -> dict[str, str]:
    out = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        capture_output=True, check=True,
    ).stdout
    files: dict[str, str] = {}
    for raw in out.split(b"\0"):
        if not raw:
            continue
        path = raw.decode("utf-8", errors="surrogateescape")
        if is_excluded(path):
            continue
        full = root / path
        if not full.is_file():
            continue  # deleted in the work tree, or a submodule
        data = full.read_bytes()
        if b"\0" in data[:8000]:
            continue  # binary
        files[path] = data.decode("utf-8", errors="replace")
    return files


def run_check(root: Path) -> int:
    decisions = root / DECISIONS_FILE
    if not decisions.is_file():
        print(f"error: {DECISIONS_FILE} not found under {root}", file=sys.stderr)
        return 2
    entries = parse_entries(decisions.read_text(encoding="utf-8"))
    files = load_repo(root)
    ref_count = sum(1 for text in files.values() for _ in find_refs(text))
    problems = check(files, entries)
    print(f"entries in {DECISIONS_FILE}: {len(entries)} "
          f"(min {min(entries, default=0):03d}, max {max(entries, default=0):03d})")
    print(f"files scanned: {len(files)}, ADR references found: {ref_count}")
    if problems:
        print(f"FAIL: {len(problems)} reference(s) point to a missing entry:")
        for p in problems:
            print(f"  {p}")
        return 1
    print("OK: every ADR reference has an entry")
    return 0


def self_test() -> int:
    # Test strings are built from A = "ADR" so that this file, once tracked, does
    # not itself contain references to missing numbers.
    A = "ADR"
    decisions = "\n".join([
        "# Decisions",
        f"## {A}-001 · first",
        f"## {A}-007 · seventh",
        "### note",
        f"#### {A}-009 · verbatim copy kept inside another entry, not an entry",
        "```",
        f"## {A}-010 · inside a code block, not an entry",
        "```",
        f"## {A}-043 · after the gap",
    ])
    entries = parse_entries(decisions)
    cases = [
        # (name, files, expect_fail)
        ("all references exist",
         {"a.md": f"see {A}-001 and {A}-007\nalso {A}-043", "b.py": f"# {A}-007"},
         False),
        ("reference to a missing number (deliberately wrong)",
         {"a.md": f"see {A}-001", "b.py": f"# per {A}-999 we do this"},
         True),
        (f"spelling variants {A}-7 / {A}-007 / {A.lower()}-007 resolve to the same entry",
         {"a.md": f"{A}-7, {A}-007, {A.lower()}-007, {A}\u2013007"},
         False),
        ("reserved gap number 042 is reported (deliberately wrong)",
         {"a.md": f"{A}-042"},
         True),
        (f"{A}-42 without zero padding is also reported (deliberately wrong)",
         {"a.md": f"{A}-42"},
         True),
        ("a level-4 heading is not an entry (deliberately wrong)",
         {"a.md": f"{A}-009"},
         True),
        ("a heading inside a code block is not an entry (deliberately wrong)",
         {"a.md": f"{A}-010"},
         True),
        (f"letter IDs such as {A}-D are not references",
         {"a.md": f"{A}-D draft"},
         False),
    ]
    failed = 0
    if entries != {1, 7, 43}:
        print(f"FAIL parse_entries: got {sorted(entries)}, want [1, 7, 43]")
        failed += 1
    for name, files, expect_fail in cases:
        problems = check(files, entries)
        got_fail = bool(problems)
        ok = got_fail == expect_fail
        verdict = "fail" if got_fail else "pass"
        print(f"{'ok  ' if ok else 'FAIL'} {name}: check says {verdict}"
              + (f" ({len(problems)} problem(s), first: {problems[0]})" if problems else ""))
        if not ok:
            failed += 1
    if failed:
        print(f"self-test FAILED: {failed} case(s)")
        return 1
    print(f"self-test OK: {len(cases)} cases + entry parsing")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--self-test", action="store_true", help="run built-in cases and exit")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent,
                        help="repository root (default: parent of scripts/)")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    return run_check(args.root)


if __name__ == "__main__":
    sys.exit(main())
