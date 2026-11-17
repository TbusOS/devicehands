"""protocol/v2 checked against itself.

The description is what both implementations are tested with, so it has to
be right on its own first: every schema is valid draft 2020-12, the sample
index matches the files on disk, valid samples pass and invalid ones fail,
breaking a valid sample makes it fail (the validator really runs), and the
regex cases in patterns.json give the answers they claim under the
reference reading (whole-string match, ASCII \\d \\w \\s).

Whether the hub and the bench agree with the description is checked in
test_v2_hub.py, test_v2_bench.py and tests/conformance.
"""

from __future__ import annotations

import copy
import re
from typing import Any

import pytest

pytest.importorskip("jsonschema")
pytest.importorskip("referencing")

from tests.protocol import schema_v2 as S


@pytest.mark.parametrize("name", sorted(S.SCHEMAS))
def test_schema_is_valid_draft_2020_12(name: str) -> None:
    schema = S.SCHEMAS[name]
    S.jsonschema.Draft202012Validator.check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == S.BASE + name


def test_index_lists_every_sample_file_once() -> None:
    on_disk = {f"{d}/{p.name}" for d in ("valid", "invalid") for p in (S.FRAMES / d).glob("*.json")}
    listed = [e["file"] for e in S.INDEX]
    assert len(listed) == len(set(listed)), "a sample is listed twice"
    assert set(listed) == on_disk
    for e in S.INDEX:
        assert e["valid"] is (e["file"].split("/")[0] == "valid"), e["file"]
        assert e["schema"].split("#")[0] in S.SCHEMAS, e["schema"]


@pytest.mark.parametrize("entry", S.INDEX, ids=[e["file"] for e in S.INDEX])
def test_sample_frame_matches_index(entry: dict[str, Any]) -> None:
    frame = S.sample(entry)
    if entry["valid"]:
        S.assert_valid(entry["schema"], frame)
    else:
        S.assert_invalid(entry["schema"], frame)


def test_every_frame_schema_has_valid_and_invalid_samples() -> None:
    frame_schemas = S.CONTROL_SCHEMAS | S.EVENT_SCHEMAS | {"job-request.json"}
    valid = {e["schema"] for e in S.INDEX if e["valid"]}
    invalid = {e["schema"] for e in S.INDEX if not e["valid"]}
    assert frame_schemas - valid == set()
    assert frame_schemas - invalid == set()


def test_the_verbs_are_the_v2_set() -> None:
    """DESIGN.md 3.5 removed three verbs and 3.3 added `error`."""
    assert sorted(S.CONTROL_VERBS) == sorted(
        [
            "hello",
            "hello_ok",
            "error",
            "heartbeat",
            "list_adb",
            "adb_list",
            "list_com",
            "com_list",
            "restart_adb",
            "open_channel",
            "channel_error",
        ]
    )
    assert S.JOB_EVENTS == {"accepted", "progress", "file", "done"}


def test_no_frame_carries_a_version_or_a_credential() -> None:
    """DESIGN.md 3.1 / 3.2: no `v` in any frame, no token anywhere in a frame."""
    for name in S.CONTROL_SCHEMAS:
        props = S.SCHEMAS[name]["properties"]
        assert "v" not in props, name
        assert not {"token", "agent_id", "role"} & set(props), name


@pytest.mark.parametrize(
    "entry", S.samples(valid=True, schemas=S.CONTROL_SCHEMAS), ids=lambda e: e["file"]
)
def test_valid_control_sample_passes_the_dispatch_schema(entry: dict[str, Any]) -> None:
    S.assert_valid("control.json", S.sample(entry))


@pytest.mark.parametrize(
    "entry", S.samples(valid=True, schemas=S.EVENT_SCHEMAS), ids=lambda e: e["file"]
)
def test_valid_job_event_sample_passes_the_dispatch_schema(entry: dict[str, Any]) -> None:
    S.assert_valid("job-event.json", S.sample(entry))


def _break(entry: dict[str, Any]) -> Any:
    """A broken copy of a valid sample: the last required field removed (the
    first is `verb` for control frames, which would only test the dispatch)."""
    frame = copy.deepcopy(S.sample(entry))
    target: Any = S.SCHEMAS[entry["schema"].split("#")[0]]
    if "#" in entry["schema"]:
        for part in entry["schema"].split("#/")[1].split("/"):
            target = target[part]
    if target.get("required"):
        del frame[target["required"][-1]]
    else:
        frame = ["not", "an", "object"]
    return frame


@pytest.mark.parametrize("entry", S.samples(valid=True), ids=lambda e: e["file"])
def test_self_check_breaking_a_valid_sample_makes_it_fail(entry: dict[str, Any]) -> None:
    """Guards against a validator that accepts everything (a wrong $ref base,
    a schema that resolved to `true`)."""
    S.assert_valid(entry["schema"], S.sample(entry))
    S.assert_invalid(entry["schema"], _break(entry))


def test_self_check_nested_reference_is_enforced() -> None:
    """A bad op row deep inside hello must fail hello: the cross-file $ref
    from hello.json to op-table.json resolves instead of being skipped."""
    hello = S.sample({"file": "valid/hello.json"})
    S.assert_valid("hello.json", hello)
    hello["ops"][0]["params"] = [{"name": "x", "kind": "float", "required": True, "default": None}]
    S.assert_invalid("hello.json", hello)
    S.assert_invalid("control.json", hello)


def test_binary_job_frames_are_unchanged_from_v1() -> None:
    """DESIGN.md 3 changes nothing in the job-frame layout."""
    import json

    v1 = json.loads((S.REPO / "protocol/v1/frames/binary/job-frames.json").read_text("utf-8"))
    assert S.BINARY["max_payload"] == v1["max_payload"] == 8 * 1024 * 1024
    assert S.BINARY["valid"] == v1["valid"]
    assert S.BINARY["invalid"] == v1["invalid"]


# ── patterns.json ────────────────────────────────────────────────────


def _reference_match(pattern: str, value: str) -> bool:
    """The reading framing.md section 7 defines, spelled the ECMA / Rust way
    (wrapped and anchored) so it is not the same call the implementations make."""
    return re.match(f"(?:{pattern})\\Z", value, re.ASCII) is not None


@pytest.mark.parametrize(
    "case",
    S.PATTERNS["cases"],
    ids=[f"{i}:{c['pattern'][:20]}|{c['input'][:12]!r}" for i, c in enumerate(S.PATTERNS["cases"])],
)
def test_pattern_case_holds_under_the_reference_reading(case: dict[str, Any]) -> None:
    assert _reference_match(case["pattern"], case["input"]) is case["match"]


def test_patterns_file_covers_both_answers_and_every_case_pattern_is_allowed() -> None:
    cases = S.PATTERNS["cases"]
    assert {c["match"] for c in cases} == {True, False}
    allowed = set(S.PATTERNS["allowed"])
    refused = {r["pattern"] for r in S.PATTERNS["refused"]}
    assert not allowed & refused
    # The partition rule rides the wire now (DESIGN.md 3.6); its cases are here.
    assert "[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}" in allowed
    assert {c["pattern"] for c in cases} <= allowed


def test_every_pattern_in_the_samples_is_an_allowed_one() -> None:
    allowed = set(S.PATTERNS["allowed"])
    for entry in S.samples(valid=True):
        frame = S.sample(entry)
        ops = frame.get("ops", [frame]) if isinstance(frame, dict) else []
        for op in ops:
            for p in op.get("params", []) if isinstance(op, dict) else []:
                if "pattern" in p:
                    assert p["pattern"] in allowed, (entry["file"], p["pattern"])
