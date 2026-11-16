"""scripts/check_sensitive_words.sh keeps matching what it is meant to match.

The script's own sample lines (SELF_TEST_CASES, run with --self-test) hold
the cases, hits and misses both; this test only runs them, so the sample
paths stay in the one file the scan itself skips.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_sensitive_words.sh"


@pytest.mark.skipif(sys.platform == "win32" or not shutil.which("bash"), reason="needs bash")
def test_self_test_passes() -> None:
    r = subprocess.run(["bash", str(_SCRIPT), "--self-test"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    last = r.stdout.strip().splitlines()[-1]
    assert last.startswith("self-test: ") and last.endswith(" passed"), last
    done, total = last.removeprefix("self-test: ").removesuffix(" passed").split("/")
    assert done == total and int(total) >= 8, last
