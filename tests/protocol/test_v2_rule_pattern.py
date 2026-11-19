"""The hub's reading of parameter rules (devicehands.remote.rule_pattern)
against protocol/v2/patterns.json: the same file the bench is held to."""

from __future__ import annotations

from typing import Any

import pytest

from devicehands.remote import rule_pattern
from tests.protocol.schema_v2 import PATTERNS


@pytest.mark.parametrize("pattern", PATTERNS["allowed"])
def test_allowed_pattern_is_inside_the_subset(pattern: str) -> None:
    assert rule_pattern.problem(pattern) == ""


@pytest.mark.parametrize("entry", PATTERNS["refused"], ids=lambda e: e["why"][:40])
def test_refused_pattern_is_outside_the_subset(entry: dict[str, Any]) -> None:
    assert rule_pattern.problem(entry["pattern"]) != ""


@pytest.mark.parametrize(
    "case",
    PATTERNS["cases"],
    ids=[f"{i}:{c['pattern'][:20]}|{c['input'][:12]!r}" for i, c in enumerate(PATTERNS["cases"])],
)
def test_case_gives_the_expected_answer(case: dict[str, Any]) -> None:
    assert rule_pattern.fullmatch(case["pattern"], case["input"]) is case["match"]


def test_a_problem_names_where_it_is() -> None:
    assert "offset 3" in rule_pattern.problem("abc$")
