"""The v1 bench protocol description (protocol/v1) checked against both ends.

protocol/v1 holds a language-neutral description of the hub <-> bench
protocol: JSON Schemas, sample frames and byte-exact job frames. This file
checks three things:

1. The description is self-consistent: every valid sample passes its schema,
   every invalid sample fails it (driven by frames/index.json, so a new
   sample needs no test change), and breaking a valid sample makes it fail.
2. The hub (`alb.remote.protocol`, `alb.remote.jobframe`) produces and
   accepts what the description says.
3. The bench program (bench-py/dhands_bench.py, loaded in-process
   by path because it cannot import alb) produces what the description says.

A test that fails because the code really disagrees with the description is
marked xfail(strict=True) with a pointer into protocol/v1/known-gaps.md. When
the code is fixed the test starts passing, strict xfail turns that into a
failure, and the marker has to be removed.
"""

from __future__ import annotations

import copy
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

from devicehands.remote import bench_ops, jobframe  # noqa: E402
from devicehands.remote import protocol as P  # noqa: E402

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


# ── 2. the hub ───────────────────────────────────────────────────────


_HUB_CONTROL = [
    ("hello_ok.json", lambda: P.hello_ok(server_version=P.PROTOCOL_VERSION)),
    ("heartbeat.json", P.heartbeat),
    ("list_adb.json", P.list_adb),
    ("list_com.json", P.list_com),
    ("com_list.json", lambda: P.com_list([{"port": "COM3", "desc": "USB Serial Port"}])),
    ("restart_adb.json", lambda: P.restart_adb(kill_conflicts=True)),
    ("restart_adb.json", P.restart_adb),
    *[
        (
            "open_channel.json",
            (
                lambda ctype=ctype, params=params: P.open_channel(
                    cid=P.new_cid(),
                    csecret=P.new_csecret(),
                    ctype=ctype,
                    role=P.default_role(ctype),
                    params=params,
                )
            ),
        )
        for ctype, params in (
            (P.ChannelType.TCP, {"target": P.ADB_TARGET}),
            (P.ChannelType.SERIAL, {"com": "COM3", "baud": 115200}),
            (P.ChannelType.JOB, {"op": "flash"}),
        )
    ],
    ("channel_error.json", lambda: P.channel_error(cid=P.new_cid(), reason="example")),
    ("channel_opened.json", lambda: P.channel_opened(cid=P.new_cid())),
    ("close_channel.json", lambda: P.close_channel(cid=P.new_cid())),
    ("channel_closed.json", lambda: P.channel_closed(cid=P.new_cid())),
]


@pytest.mark.parametrize(("schema", "build"), _HUB_CONTROL, ids=[s for s, _ in _HUB_CONTROL])
def test_hub_control_builder_matches_schema(schema: str, build: Any) -> None:
    frame = build()
    _assert_valid(schema, frame)
    _assert_valid("control.json", frame)
    assert P.decode_control(P.encode_control(frame)) == frame


@pytest.mark.xfail(
    strict=True,
    reason=f"{_GAPS} G2: protocol.hello() lacks build/ops/flash_partitions/boards/update",
)
def test_hub_hello_builder_matches_schema() -> None:
    frame = P.hello(agent_id="bench-1", name="bench one", version=1, caps=["adb"], token=None)
    _assert_valid("hello.json", frame)


@pytest.mark.xfail(strict=True, reason=f"{_GAPS} G3: protocol.adb_list() lacks conflicts")
def test_hub_adb_list_builder_matches_schema() -> None:
    _assert_valid("adb_list.json", P.adb_list(["EXAMPLE0001"]))


_HUB_JOB = [
    ("job-request.json", lambda: P.job_flash(partition="boot_a", size=4096, sha256=_SHA)),
    ("job-request.json", lambda: P.job_reboot(target="bootloader")),
    ("job-request.json", P.job_devices),
    ("job-request.json", lambda: P.job_getvar(name="")),
    ("job-request.json", lambda: P.job_oem(command="device-info")),
    ("job-request.json", lambda: P.job_erase(partition="misc")),
    ("job-request.json", lambda: P.job_op("log-tail", {"source": "agent", "lines": 50})),
    ("job-request.json", lambda: P.job_op("tool-run", {"tool": "example-tool", "args": ["-v"]})),
    ("job-request.json", lambda: P.job_op("boot", {}, size=4096, sha256=_SHA)),
    ("job-accepted.json", P.job_accepted),
    ("job-progress.json", lambda: P.job_progress(phase=P.JobPhase.TRANSFER, done=1, total=2)),
    (
        "job-progress.json",
        lambda: P.job_progress(phase=P.JobPhase.FLASH, done=0, total=0, text="x"),
    ),
    ("job-done.json", lambda: P.job_done(ok=True, rc=0)),
    ("job-done.json", lambda: P.job_done(ok=False, rc=1, error="x", code="FLASH_FAILED", data={})),
]


@pytest.mark.parametrize(("schema", "build"), _HUB_JOB, ids=[s for s, _ in _HUB_JOB])
def test_hub_job_builder_matches_schema(schema: str, build: Any) -> None:
    frame = json.loads(json.dumps(build()))  # enum members -> plain JSON values
    _assert_valid(schema, frame)
    if schema != "job-request.json":
        _assert_valid("job-event.json", frame)


@pytest.mark.parametrize(
    "entry",
    [e for e in _INDEX if e["valid"] and e["schema"] in _CONTROL_SCHEMAS],
    ids=lambda e: e["file"],
)
def test_hub_decode_control_accepts_valid_sample(entry: dict[str, Any]) -> None:
    frame = _sample(entry)
    assert P.decode_control(json.dumps(frame, ensure_ascii=False)) == frame


def test_verb_enum_matches_schema() -> None:
    assert sorted(v.value for v in P.Verb) == sorted(_CONTROL_VERBS)


def test_channel_enums_match_schema() -> None:
    props = _SCHEMAS["open_channel.json"]["properties"]
    assert sorted(t.value for t in P.ChannelType) == sorted(props["channel_type"]["enum"])
    assert sorted(r.value for r in P.ChannelRole) == sorted(props["role"]["enum"])


def test_op_table_enums_match_schema() -> None:
    defs = _SCHEMAS["op-table.json"]["$defs"]
    assert sorted(bench_ops.KINDS) == sorted(defs["ParamDesc"]["properties"]["kind"]["enum"])
    assert sorted(bench_ops.RECORD_TIERS) == sorted(defs["OpDesc"]["properties"]["record"]["enum"])


def _wire_events() -> set[str]:
    return {_SCHEMAS[ref]["properties"]["ev"]["const"] for ref in _EVENT_SCHEMAS}


def _wire_phases() -> set[str]:
    return set(_SCHEMAS["job-progress.json"]["properties"]["phase"]["enum"])


def test_job_enums_are_a_subset_of_schema() -> None:
    assert {e.value for e in P.JobEvent} <= _wire_events()
    assert {p.value for p in P.JobPhase} <= _wire_phases()


@pytest.mark.xfail(strict=True, reason=f"{_GAPS} G4: JobEvent has no 'file'")
def test_job_event_enum_covers_every_wire_event() -> None:
    assert {e.value for e in P.JobEvent} == _wire_events()


@pytest.mark.xfail(strict=True, reason=f"{_GAPS} G5: JobPhase has no 'edl' / 'tool'")
def test_job_phase_enum_covers_every_wire_phase() -> None:
    assert {p.value for p in P.JobPhase} == _wire_phases()


# ── 3. the bench program ─────────────────────────────────────────────


def _load_agent() -> Any:
    spec = importlib.util.spec_from_file_location("alb_agent_protocol_under_test", _AGENT_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


agent = _load_agent()


def test_shared_constants_agree() -> None:
    """A few of the hand-copied constants the bench mirrors from the hub."""
    assert (
        agent.PROTOCOL_VERSION
        == P.PROTOCOL_VERSION
        == _SCHEMAS["common.json"]["$defs"]["v"]["const"]
    )
    assert (agent.TOKEN_HEADER, agent.CSECRET_HEADER) == (P.TOKEN_HEADER, P.CSECRET_HEADER)
    assert agent.ADB_TARGET == P.ADB_TARGET
    assert agent._PARTITION_RE.pattern == bench_ops.PARTITION_RE.pattern
    assert agent._JOB_HEADER.format == jobframe._HEADER.format
    assert agent._JOB_MAX_FRAME == jobframe.MAX_FRAME
    assert sorted(agent._PARAM_KINDS) == sorted(bench_ops.KINDS)


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
    assert P.decode_control(json.dumps(full_hello)) == full_hello


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


def test_binary_cap_matches_both_ends() -> None:
    assert _BINARY["max_payload"] == jobframe.MAX_FRAME == agent._JOB_MAX_FRAME


@pytest.mark.parametrize("case", _BINARY["valid"], ids=lambda c: c["name"])
def test_both_encoders_produce_the_golden_bytes(case: dict[str, Any]) -> None:
    want = bytes.fromhex(case["hex"])
    if case["kind"] == "C":
        msg = json.loads(case["payload_text"])
        assert jobframe.encode_control(msg) == want
        assert agent._job_control(msg) == want
    else:
        assert jobframe.encode_data(_payload(case)) == want
        assert agent._job_encode(b"D", _payload(case)) == want


@pytest.mark.parametrize("case", _BINARY["valid"], ids=lambda c: c["name"])
async def test_both_readers_decode_the_golden_bytes(case: dict[str, Any]) -> None:
    data = bytes.fromhex(case["hex"])
    want = (case["kind"].encode(), _payload(case))
    assert await jobframe.FrameReader(_ByteStream(data)).read() == want
    assert await agent._JobReader(_ByteStream(data)).read() == want


@pytest.mark.parametrize("case", _BINARY["invalid"], ids=lambda c: c["name"])
async def test_both_readers_reject_the_bad_bytes(case: dict[str, Any]) -> None:
    data = bytes.fromhex(case["hex"])
    with pytest.raises(jobframe.JobProtocolError):
        await jobframe.FrameReader(_ByteStream(data)).read()
    with pytest.raises(agent._JobProtocolError):
        await agent._JobReader(_ByteStream(data)).read()
