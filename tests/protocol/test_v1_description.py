"""protocol/v1, kept as history, checked against itself.

v1 describes the protocol before version 2 (protocol/README.md). The code no
longer speaks it — the hub and the bench are held to protocol/v2 by
test_v2_*.py and tests/conformance — but the description stays readable and
consistent: every schema is valid, the sample index matches the files, valid
samples pass, invalid ones fail, and breaking a valid sample makes it fail.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

jsonschema = pytest.importorskip("jsonschema")
referencing = pytest.importorskip("referencing")

from referencing import Registry, Resource  # noqa: E402
from referencing.jsonschema import DRAFT202012  # noqa: E402

_REPO = Path(__file__).resolve().parents[2]
_V1 = _REPO / "protocol" / "v1"
_SCHEMA_DIR = _V1 / "schema"
_FRAMES = _V1 / "frames"
_BASE = "https://devicehands.invalid/protocol/v1/schema/"


# ── schema loading ───────────────────────────────────────────────────


def _load_schemas() -> dict[str, dict[str, Any]]:
    return {
        p.name: json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(_SCHEMA_DIR.glob("*.json"))
    }


_SCHEMAS = _load_schemas()
_REGISTRY: Registry = Registry().with_resources(
    (s["$id"], Resource.from_contents(s, default_specification=DRAFT202012))
    for s in _SCHEMAS.values()
)


def _errors(ref: str, instance: Any) -> list[str]:
    """Validation errors of `instance` against `ref`, a schema file name with
    an optional JSON-pointer fragment ("op-table.json#/$defs/OpDesc")."""
    validator = jsonschema.Draft202012Validator({"$ref": _BASE + ref}, registry=_REGISTRY)
    return [
        f"{'/'.join(map(str, e.absolute_path))}: {e.message}"
        for e in validator.iter_errors(instance)
    ]


def _assert_valid(ref: str, instance: Any) -> None:
    errors = _errors(ref, instance)
    assert not errors, f"{ref}: {errors}"


def _assert_invalid(ref: str, instance: Any) -> None:
    assert _errors(ref, instance), f"{ref} accepted an instance it must reject: {instance!r}"


_INDEX = json.loads((_FRAMES / "index.json").read_text(encoding="utf-8"))["frames"]
_CONTROL_VERBS = [
    _SCHEMAS[ref["$ref"]]["properties"]["verb"]["const"]
    for ref in _SCHEMAS["control.json"]["oneOf"]
]
_CONTROL_SCHEMAS = {f"{verb}.json" for verb in _CONTROL_VERBS}
_EVENT_SCHEMAS = {ref["$ref"] for ref in _SCHEMAS["job-event.json"]["oneOf"]}


def _sample(entry: dict[str, Any]) -> Any:
    return json.loads((_FRAMES / entry["file"]).read_text(encoding="utf-8"))


# ── 1. the description itself ───────────────────────────────────────


@pytest.mark.parametrize("name", sorted(_SCHEMAS))
def test_schema_is_valid_draft_2020_12(name: str) -> None:
    schema = _SCHEMAS[name]
    jsonschema.Draft202012Validator.check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == _BASE + name


def test_index_lists_every_sample_file_once() -> None:
    on_disk = {f"{d}/{p.name}" for d in ("valid", "invalid") for p in (_FRAMES / d).glob("*.json")}
    listed = [e["file"] for e in _INDEX]
    assert len(listed) == len(set(listed)), "a sample is listed twice"
    assert set(listed) == on_disk
    for e in _INDEX:
        assert e["valid"] is (e["file"].split("/")[0] == "valid"), e["file"]
        assert e["schema"].split("#")[0] in _SCHEMAS, e["schema"]


@pytest.mark.parametrize("entry", _INDEX, ids=[e["file"] for e in _INDEX])
def test_sample_frame_matches_index(entry: dict[str, Any]) -> None:
    frame = _sample(entry)
    if entry["valid"]:
        _assert_valid(entry["schema"], frame)
    else:
        _assert_invalid(entry["schema"], frame)


def test_every_frame_schema_has_valid_and_invalid_samples() -> None:
    frame_schemas = _CONTROL_SCHEMAS | _EVENT_SCHEMAS | {"job-request.json"}
    valid = {e["schema"] for e in _INDEX if e["valid"]}
    invalid = {e["schema"] for e in _INDEX if not e["valid"]}
    assert frame_schemas - valid == set()
    assert frame_schemas - invalid == set()


@pytest.mark.parametrize(
    "entry",
    [e for e in _INDEX if e["valid"] and e["schema"] in _CONTROL_SCHEMAS],
    ids=lambda e: e["file"],
)
def test_valid_control_sample_passes_the_dispatch_schema(entry: dict[str, Any]) -> None:
    _assert_valid("control.json", _sample(entry))


@pytest.mark.parametrize(
    "entry",
    [e for e in _INDEX if e["valid"] and e["schema"] in _EVENT_SCHEMAS],
    ids=lambda e: e["file"],
)
def test_valid_job_event_sample_passes_the_dispatch_schema(entry: dict[str, Any]) -> None:
    _assert_valid("job-event.json", _sample(entry))


def _break(entry: dict[str, Any]) -> Any:
    """A broken copy of a valid sample: the first required field removed."""
    frame = copy.deepcopy(_sample(entry))
    target = _SCHEMAS[entry["schema"].split("#")[0]]
    if "#" in entry["schema"]:
        for part in entry["schema"].split("#/")[1].split("/"):
            target = target[part]
    if "required" in target:
        del frame[target["required"][0]]
    else:
        frame = ["not", "an", "object"]
    return frame


@pytest.mark.parametrize("entry", [e for e in _INDEX if e["valid"]], ids=lambda e: e["file"])
def test_self_check_breaking_a_valid_sample_makes_it_fail(entry: dict[str, Any]) -> None:
    """Guards against a validator that accepts everything (a wrong $ref base,
    a schema that resolved to `true`): the untouched sample passes, the
    same sample with a required field removed must not."""
    _assert_valid(entry["schema"], _sample(entry))
    _assert_invalid(entry["schema"], _break(entry))


def test_self_check_nested_reference_is_enforced() -> None:
    """A bad op row deep inside hello must fail hello: proves the cross-file
    $ref from hello.json to op-table.json resolves instead of being skipped."""
    hello = _sample({"file": "valid/hello.json"})
    _assert_valid("hello.json", hello)
    hello["ops"][0]["params"] = [{"name": "x", "kind": "float", "required": True, "default": None}]
    _assert_invalid("hello.json", hello)
    hello = _sample({"file": "valid/hello.json"})
    hello["v"] = 2
    _assert_invalid("hello.json", hello)
    _assert_invalid("control.json", hello)
