"""Load the protocol/v2 description for tests: schemas, samples, patterns.

Shared by the description self-check, the hub and bench checks in
tests/protocol, and the conformance suite in tests/conformance. Needs
`jsonschema` (with `referencing`); importers skip when it is missing.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

REPO = Path(__file__).resolve().parents[2]
V2 = REPO / "protocol" / "v2"
SCHEMA_DIR = V2 / "schema"
FRAMES = V2 / "frames"
BASE = "https://devicehands.invalid/protocol/v2/schema/"
# No type stubs for jsonschema here; importing it by name keeps mypy quiet
# without an ignore comment that would go stale once stubs are installed.
jsonschema: Any = importlib.import_module("jsonschema")


def _load_schemas() -> dict[str, dict[str, Any]]:
    return {
        p.name: json.loads(p.read_text(encoding="utf-8")) for p in sorted(SCHEMA_DIR.glob("*.json"))
    }


SCHEMAS = _load_schemas()
REGISTRY: Registry = Registry().with_resources(
    (s["$id"], Resource.from_contents(s, default_specification=DRAFT202012))
    for s in SCHEMAS.values()
)
INDEX: list[dict[str, Any]] = json.loads((FRAMES / "index.json").read_text(encoding="utf-8"))[
    "frames"
]
PATTERNS: dict[str, Any] = json.loads((V2 / "patterns.json").read_text(encoding="utf-8"))
BINARY: dict[str, Any] = json.loads(
    (FRAMES / "binary" / "job-frames.json").read_text(encoding="utf-8")
)
CONTROL_VERBS = [
    SCHEMAS[r["$ref"]]["properties"]["verb"]["const"] for r in SCHEMAS["control.json"]["oneOf"]
]
CONTROL_SCHEMAS = {f"{verb}.json" for verb in CONTROL_VERBS}
EVENT_SCHEMAS = {r["$ref"] for r in SCHEMAS["job-event.json"]["oneOf"]}
JOB_EVENTS = {SCHEMAS[r]["properties"]["ev"]["const"] for r in EVENT_SCHEMAS}


def errors(ref: str, instance: Any) -> list[str]:
    """Validation errors of `instance` against `ref`, a schema file name with
    an optional JSON-pointer fragment ("op-table.json#/$defs/OpDesc")."""
    validator = jsonschema.Draft202012Validator({"$ref": BASE + ref}, registry=REGISTRY)
    return [
        f"{'/'.join(map(str, e.absolute_path))}: {e.message}"
        for e in validator.iter_errors(instance)
    ]


def assert_valid(ref: str, instance: Any) -> None:
    errs = errors(ref, instance)
    assert not errs, f"{ref}: {errs}"


def assert_invalid(ref: str, instance: Any) -> None:
    assert errors(ref, instance), f"{ref} accepted an instance it must reject: {instance!r}"


def sample(entry: dict[str, Any]) -> Any:
    return json.loads((FRAMES / entry["file"]).read_text(encoding="utf-8"))


def samples(*, valid: bool, schemas: set[str] | None = None) -> list[dict[str, Any]]:
    return [e for e in INDEX if e["valid"] is valid and (schemas is None or e["schema"] in schemas)]


def control_schema_of(frame: Any) -> str:
    """The per-verb schema a control frame belongs to ("" when the verb is unknown)."""
    if isinstance(frame, dict) and f"{frame.get('verb')}.json" in CONTROL_SCHEMAS:
        return f"{frame['verb']}.json"
    return ""
