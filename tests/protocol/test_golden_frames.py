"""The v1 bench protocol description (protocol/v1) checked against both ends.

protocol/v1 holds a language-neutral description of the hub <-> bench
protocol: JSON Schemas, sample frames and byte-exact job frames. This file
checks three things:

1. The description is self-consistent: every valid sample passes its schema,
   every invalid sample fails it (driven by frames/index.json, so a new
   sample needs no test change), and breaking a valid sample makes it fail.
2. (moved) The hub speaks v2 now: tests/protocol/test_v2_hub.py.
3. The bench program (bench-py/dhands_bench.py, loaded in-process
   by path because it cannot import alb) produces what the description says.

A test that fails because the code really disagrees with the description is
marked xfail(strict=True) with a pointer into protocol/v1/known-gaps.md. When
the code is fixed the test starts passing, strict xfail turns that into a
failure, and the marker has to be removed.
"""

from __future__ import annotations

import importlib.util
import json
import struct
import sys
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
_GAPS = "protocol/v1/known-gaps.md"
_AGENT_PATH = _REPO / "bench-py" / "dhands_bench.py"

_CID = "0123456789abcdef0123456789abcdef"
_SHA = "ab" * 32


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


# ── 3. the bench program ─────────────────────────────────────────────


def _load_agent() -> Any:
    spec = importlib.util.spec_from_file_location("alb_agent_protocol_under_test", _AGENT_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


agent = _load_agent()


def _full_table(tmp_path: Path) -> dict[str, Any]:
    """Every row the bench can offer: fastboot, host, EDL and vendor tool."""
    for name in ("sahara.exe", "firehose.exe", "tool.exe", "agent.log"):
        (tmp_path / name).write_text("")
    edl, problem = agent._resolve_edl(
        str(tmp_path / "sahara.exe"), str(tmp_path / "firehose.exe"), str(tmp_path)
    )
    assert problem == ""
    tools, problems = agent._resolve_tools(
        {"example-tool": {"tool_exe": str(tmp_path / "tool.exe"), "tool_root": str(tmp_path)}}
    )
    assert not problems
    table: dict[str, Any] = agent._build_ops(
        "/opt/example/fastboot", {"agent": tmp_path / "agent.log"}, edl, tools
    )
    return table


@pytest.fixture
def full_hello(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    table = _full_table(tmp_path)
    monkeypatch.setattr(agent, "_OPS", table)
    monkeypatch.setattr(agent, "_FASTBOOT_PATH", "/opt/example/fastboot")
    monkeypatch.setattr(agent, "_BOARD_PRODUCT", {"board-a": "EXAMPLE-PRODUCT"})
    monkeypatch.setattr(
        agent,
        "_LAST_UPDATE",
        {
            "id": "update-0001",
            "outcome": "pending",
            "from_build": "ba9876543210",
            "to_build": "0123456789ab",
            "at": 1700000000.5,
        },
    )
    frame: dict[str, Any] = json.loads(agent._hello("bench-1", "bench one", "example-token"))
    return frame


def test_bench_hello_with_every_op_matches_schema(full_hello: dict[str, Any]) -> None:
    assert len(full_hello["ops"]) == 16
    assert {op["group"] for op in full_hello["ops"]} == {"fastboot", "host", "edl", "tool"}
    _assert_valid("hello.json", full_hello)
    _assert_valid("control.json", full_hello)


def test_bench_hello_without_tools_matches_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent, "_OPS", agent._build_ops("", {}))
    monkeypatch.setattr(agent, "_FASTBOOT_PATH", "")
    monkeypatch.setattr(agent, "_BOARD_PRODUCT", {})
    monkeypatch.setattr(agent, "_LAST_UPDATE", {})
    frame = json.loads(agent._hello("bench-2", "", None))
    assert frame["caps"] == ["adb"] and frame["update"] is None and frame["token"] is None
    _assert_valid("hello.json", frame)


_PYTHON_ONLY_REGEX = ("\\Z", "\\A", "(?P<", "(?P=")


@pytest.mark.xfail(strict=True, reason=f"{_GAPS} G9: getvar pattern uses Python-only \\Z")
def test_bench_op_patterns_use_no_python_only_syntax(full_hello: dict[str, Any]) -> None:
    patterns = [
        p["pattern"] for op in full_hello["ops"] for p in op["params"] if p["kind"] == "text"
    ]
    assert patterns, "no text parameter in the table: this check would be vacuous"
    offending = [p for p in patterns if any(tok in p for tok in _PYTHON_ONLY_REGEX)]
    assert offending == []


class _ControlWs:
    """Signaling-socket stand-in: collects what the bench sends."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(self, text: str) -> None:
        self.sent.append(json.loads(text))


def test_bench_heartbeat_matches_schema() -> None:
    _assert_valid("heartbeat.json", json.loads(agent._frame("heartbeat")))


async def test_bench_channel_error_matches_schema() -> None:
    ws = _ControlWs()
    await agent._report_channel_error(ws, _CID, "cannot open COM3 @ 115200: example")
    assert len(ws.sent) == 1
    _assert_valid("channel_error.json", ws.sent[0])


@pytest.mark.parametrize("devices", [["EXAMPLE0001"], []])
async def test_bench_adb_list_matches_schema(
    devices: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_devices() -> list[str]:
        return devices

    monkeypatch.setattr(agent, "_adb_devices", fake_devices)
    monkeypatch.setattr(agent, "_find_adb_conflicts", lambda: ["vendor_adb.exe pid=4242"])
    ws = _ControlWs()
    await agent._reply_adb_list(ws)
    assert len(ws.sent) == 1
    _assert_valid("adb_list.json", ws.sent[0])


async def test_bench_com_list_matches_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        agent, "_enumerate_com", lambda: [{"port": "COM3", "desc": "USB Serial Port (COM3)"}]
    )
    ws = _ControlWs()
    await agent._reply_com_list(ws)
    _assert_valid("com_list.json", ws.sent[0])


_HDR = struct.Struct(">cI")


class _JobWs:
    """Job data-socket stand-in: feeds `incoming` once, collects what is sent."""

    def __init__(self, incoming: bytes = b"") -> None:
        self._incoming = [incoming] if incoming else []
        self.out = bytearray()

    async def send(self, data: bytes) -> None:
        self.out.extend(data)

    async def recv(self) -> bytes:
        return self._incoming.pop(0) if self._incoming else b""

    def control_frames(self) -> list[dict[str, Any]]:
        frames, buf = [], bytes(self.out)
        while buf:
            kind, length = _HDR.unpack(buf[:5])
            assert kind == b"C", "the bench sends only control frames on a job channel"
            frames.append(json.loads(buf[5 : 5 + length]))
            buf = buf[5 + length :]
        return frames


def _request(**fields: Any) -> bytes:
    payload = json.dumps(fields).encode()
    return _HDR.pack(b"C", len(payload)) + payload


async def test_bench_progress_and_done_match_schema() -> None:
    ws = _JobWs()
    await agent._job_progress(ws, "transfer", 65536, 131072)
    await agent._job_finish(ws, 0, "out", "", fail_code="")
    await agent._job_finish(ws, 1, "", "example error", fail_code="FLASH_FAILED")
    await agent._job_fail(ws, "bad opening frame: example", code="")
    frames = ws.control_frames()
    assert [f["ev"] for f in frames] == ["progress", "done", "done", "done"]
    _assert_valid("job-progress.json", frames[0])
    for f in frames[1:]:
        _assert_valid("job-done.json", f)
        _assert_valid("job-event.json", f)


async def test_bench_unknown_op_answer_matches_schema() -> None:
    ws = _JobWs(_request(op="no-such-op"))
    await agent._run_job(ws, {})
    (done,) = ws.control_frames()
    assert done["code"] == "BENCH_OP_UNKNOWN"
    _assert_valid("job-done.json", done)


async def test_bench_builtin_op_answers_match_schema() -> None:
    async def probe(_spec: Any, _values: Any) -> tuple[str, dict[str, Any]]:
        return "probe output", {"answer": 42}

    spec = agent.OpSpec(
        "probe", "host", "example builtin", mutates=False, exclusive=False, builtin=probe
    )
    ws = _JobWs(_request(op="probe"))
    await agent._run_job(ws, {"probe": spec}, hint="probe")
    frames = ws.control_frames()
    assert [f["ev"] for f in frames] == ["accepted", "done"]
    _assert_valid("job-accepted.json", frames[0])
    _assert_valid("job-done.json", frames[1])
    assert frames[1]["data"] == {"answer": 42}


async def test_bench_file_frames_match_schema(tmp_path: Path) -> None:
    (tmp_path / "run.txt").write_text("example output\n")
    (tmp_path / "empty.log").write_text("")
    ws = _JobWs()
    report = await agent._tool_send_files(ws, tmp_path, {})
    assert {r["name"] for r in report if r["sent"]} == {"run.txt", "empty.log"}
    frames = ws.control_frames()
    assert {f["name"] for f in frames} == {"run.txt", "empty.log"}
    for f in frames:
        _assert_valid("job-file.json", f)
        _assert_valid("job-event.json", f)


# ── byte-exact job frames ────────────────────────────────────────────


_BINARY = json.loads((_FRAMES / "binary" / "job-frames.json").read_text(encoding="utf-8"))


def _payload(case: dict[str, Any]) -> bytes:
    if case["kind"] == "C":
        return str(case["payload_text"]).encode("utf-8")
    return bytes.fromhex(case["payload_hex"])


class _ByteStream:
    """Hands a byte string back one byte per recv(), to exercise reassembly."""

    def __init__(self, data: bytes) -> None:
        self._chunks = [data[i : i + 1] for i in range(len(data))]

    async def recv(self) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""

    async def send(self, data: bytes) -> None:  # pragma: no cover - unused
        raise AssertionError("read-only stream")


def test_binary_cap_matches_the_bench() -> None:
    assert _BINARY["max_payload"] == agent._JOB_MAX_FRAME


@pytest.mark.parametrize("case", _BINARY["valid"], ids=lambda c: c["name"])
def test_bench_encoder_produces_the_golden_bytes(case: dict[str, Any]) -> None:
    want = bytes.fromhex(case["hex"])
    if case["kind"] == "C":
        assert agent._job_control(json.loads(case["payload_text"])) == want
    else:
        assert agent._job_encode(b"D", _payload(case)) == want


@pytest.mark.parametrize("case", _BINARY["valid"], ids=lambda c: c["name"])
async def test_bench_reader_decodes_the_golden_bytes(case: dict[str, Any]) -> None:
    data = bytes.fromhex(case["hex"])
    want = (case["kind"].encode(), _payload(case))
    assert await agent._JobReader(_ByteStream(data)).read() == want


@pytest.mark.parametrize("case", _BINARY["invalid"], ids=lambda c: c["name"])
async def test_bench_reader_rejects_the_bad_bytes(case: dict[str, Any]) -> None:
    data = bytes.fromhex(case["hex"])
    with pytest.raises(agent._JobProtocolError):
        await agent._JobReader(_ByteStream(data)).read()
