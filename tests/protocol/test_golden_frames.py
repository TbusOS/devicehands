"""The bench program (bench-py/dhands_bench.py) against the protocol/v2 description.

protocol/v2 holds a language-neutral description of the hub <-> bench
protocol: JSON Schemas, sample frames, byte-exact job frames and the
parameter-rule cases in patterns.json. The bench is loaded in-process by
path (it cannot import devicehands) and checked two ways:

1. What it sends passes the schema: hello, the device lists, channel_error,
   error, heartbeat and the job events.
2. What the description calls valid it accepts, and what it calls invalid it
   refuses with the code the rules give — driven by the sample index, so a
   new sample needs no change here.

The hub's side is tests/protocol/test_v2_hub.py; the bench as a separate
process, reached only through the protocol, is tests/conformance.
"""

from __future__ import annotations

import importlib.util
import json
import struct
import sys
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("jsonschema")
pytest.importorskip("referencing")

from devicehands.remote import rule_pattern
from tests.protocol import schema_v2 as S

_AGENT_PATH = S.REPO / "bench-py" / "dhands_bench.py"
_CID = "0123456789abcdef0123456789abcdef"


def _load_agent() -> Any:
    spec = importlib.util.spec_from_file_location("dhands_bench_protocol_under_test", _AGENT_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


agent = _load_agent()


# ── hello ────────────────────────────────────────────────────────────


def _full_table(tmp_path: Path) -> dict[str, Any]:
    """Every row the bench can offer: fastboot, host, EDL and vendor tool."""
    for name in ("sahara.exe", "firehose.exe", "tool.exe", "agent.log"):
        (tmp_path / name).write_bytes(b"")
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


_UPDATE = {
    "id": "update-0001",
    "outcome": "pending",
    "from_build": "ba9876543210",
    "to_build": "0123456789ab",
    "at": 1700000000.5,
}


@pytest.fixture
def full_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    table = _full_table(tmp_path)
    monkeypatch.setattr(agent, "_OPS", table)
    monkeypatch.setattr(agent, "_FASTBOOT_PATH", "/opt/example/fastboot")
    monkeypatch.setattr(agent, "_BOARD_PRODUCT", {"board-a": "EXAMPLE-PRODUCT"})
    monkeypatch.setattr(agent, "_LAST_UPDATE", dict(_UPDATE))
    return table


@pytest.fixture
def full_hello(full_table: dict[str, Any]) -> dict[str, Any]:
    frame: dict[str, Any] = json.loads(agent._hello("bench-1", "bench one"))
    return frame


def test_bench_hello_with_every_op_matches_schema(full_hello: dict[str, Any]) -> None:
    assert len(full_hello["ops"]) == 16
    assert {op["group"] for op in full_hello["ops"]} == {"fastboot", "host", "edl", "tool"}
    S.assert_valid("hello.json", full_hello)
    S.assert_valid("control.json", full_hello)
    assert full_hello["proto"] == {"min": 2, "max": 2}
    assert full_hello["impl"]["lang"] == "python"
    assert full_hello["update"] == _UPDATE


def test_bench_hello_carries_no_v1_field(full_hello: dict[str, Any]) -> None:
    """DESIGN.md 3.2 / 3.7: no version per frame, no credential, no caps, no
    flash_partitions, no agent_version, no agent_id."""
    gone = {"v", "token", "caps", "flash_partitions", "agent_version", "agent_id", "build"}
    assert gone.isdisjoint(full_hello)


def test_bench_hello_without_tools_matches_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent, "_OPS", agent._build_ops("", {}))
    monkeypatch.setattr(agent, "_FASTBOOT_PATH", "")
    monkeypatch.setattr(agent, "_BOARD_PRODUCT", {})
    monkeypatch.setattr(agent, "_LAST_UPDATE", {})
    frame = json.loads(agent._hello("bench-2", ""))
    S.assert_valid("hello.json", frame)
    # Optional, and null is not a value it may take (hello--update-null).
    assert "update" not in frame
    assert not any(op["group"] == "fastboot" for op in frame["ops"])


def test_a_malformed_update_record_is_left_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sent as is, a broken record would make the hub refuse every hello."""
    monkeypatch.setattr(agent, "_OPS", agent._build_ops("", {}))
    monkeypatch.setattr(agent, "_BOARD_PRODUCT", {})
    monkeypatch.setattr(agent, "_LAST_UPDATE", {**_UPDATE, "outcome": "half-done"})
    frame = json.loads(agent._hello("bench-3", "x"))
    S.assert_valid("hello.json", frame)
    assert "update" not in frame


@pytest.mark.parametrize(
    ("platform", "name"), [("win32", "windows"), ("darwin", "macos"), ("linux", "linux")]
)
def test_bench_hello_names_the_os(platform: str, name: str) -> None:
    assert agent._os_name(platform) == name


# ── parameter rules: the portable subset (framing.md §7) ─────────────


def _rule_params(hello: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [(op["name"], p) for op in hello["ops"] for p in op["params"] if "pattern" in p]


def test_bench_op_patterns_are_in_the_portable_subset(full_hello: dict[str, Any]) -> None:
    """Every rule on the wire is one both ends read the same way. Checked
    with the hub's parser, not only the bench's copy of it."""
    params = _rule_params(full_hello)
    assert params, "no rule in the table: this check would be vacuous"
    for op, p in params:
        assert rule_pattern.problem(p["pattern"]) == "", (op, p)
        assert agent._rule_problem(p["pattern"]) == "", (op, p)


# Anchors (^ $) are refused by the subset check above; `^` is legal as the
# first character of a negated class, so it is not looked for here.
_PYTHON_ONLY_REGEX = ("\\Z", "\\A", "(?P<", "(?P=")


def test_bench_op_patterns_use_no_python_only_syntax(full_hello: dict[str, Any]) -> None:
    """v1 known-gap G9: the getvar rule ended in Python-only \\Z."""
    offending = [
        (op, p["pattern"])
        for op, p in _rule_params(full_hello)
        if any(tok in p["pattern"] for tok in _PYTHON_ONLY_REGEX)
    ]
    assert offending == []


def test_the_partition_rule_rides_the_wire(full_hello: dict[str, Any]) -> None:
    """DESIGN.md 3.6: the partition-name rule is the flash parameter's rule."""
    partitions = [p for _, p in _rule_params(full_hello) if p["kind"] == "partition"]
    assert partitions
    for p in partitions:
        assert p["pattern"] == agent._PARTITION_PATTERN and p["max_len"] >= 1
    assert rule_pattern.fullmatch(agent._PARTITION_PATTERN, "boot_a")
    assert not rule_pattern.fullmatch(agent._PARTITION_PATTERN, "-w")


@pytest.mark.parametrize("pattern", S.PATTERNS["allowed"])
def test_bench_rule_parser_allows(pattern: str) -> None:
    assert agent._rule_problem(pattern) == ""


@pytest.mark.parametrize("entry", S.PATTERNS["refused"], ids=lambda e: e["why"][:40])
def test_bench_rule_parser_refuses(entry: dict[str, Any]) -> None:
    assert agent._rule_problem(entry["pattern"]) != ""


@pytest.mark.parametrize(
    "case",
    S.PATTERNS["cases"],
    ids=[f"{i}:{c['pattern'][:20]}|{c['input'][:12]!r}" for i, c in enumerate(S.PATTERNS["cases"])],
)
def test_bench_rule_match_gives_the_expected_answer(case: dict[str, Any]) -> None:
    assert agent._rule_fullmatch(case["pattern"], case["input"]) is case["match"]


def test_a_text_rule_outside_the_subset_is_refused_at_start_up() -> None:
    with pytest.raises(ValueError, match="portable subset"):
        agent.ParamSpec("name", "text", pattern=r"[a-z]+\Z", max_len=8)


# ── control frames the bench sends ───────────────────────────────────


class _ControlWs:
    """Signaling-socket stand-in: collects what the bench sends."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(self, text: str) -> None:
        self.sent.append(json.loads(text))


def test_bench_heartbeat_matches_schema() -> None:
    S.assert_valid("heartbeat.json", json.loads(agent._frame("heartbeat")))


@pytest.mark.parametrize(
    ("code", "re_id"), [("BAD_FRAME", None), ("UNKNOWN_VERB", 7), ("BAD_FRAME", 0)]
)
def test_bench_error_matches_schema(code: str, re_id: int | None) -> None:
    frame = json.loads(agent._error(code, "x" * 9000, re_id))
    S.assert_valid("error.json", frame)
    assert frame.get("re") == re_id and len(frame["message"]) == 4096


async def test_bench_channel_error_matches_schema() -> None:
    ws = _ControlWs()
    await agent._report_channel_error(
        ws, _CID, "SERIAL_PORT_BUSY", "cannot open COM3 @ 115200: example"
    )
    assert len(ws.sent) == 1
    S.assert_valid("channel_error.json", ws.sent[0])


class _SerialException(OSError):
    """Stands in for pyserial's SerialException (an IOError subclass)."""


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (_SerialException(2, "could not open port /dev/ttyX: [Errno 2] No such file"), "NOT_FOUND"),
        (
            _SerialException(
                "could not open port 'COM9': FileNotFoundError(2, 'The system cannot find "
                "the file specified.', None, 2)"
            ),
            "NOT_FOUND",
        ),
        (
            _SerialException(
                "could not open port 'COM3': PermissionError(13, 'Access is denied.', None, 5)"
            ),
            "BUSY",
        ),
        (_SerialException(16, "could not open port /dev/ttyUSB0: [Errno 16] busy"), "BUSY"),
    ],
    ids=["posix-absent", "windows-absent", "windows-held", "posix-busy"],
)
def test_serial_open_failures_get_the_code_the_hub_retries_on(exc: OSError, code: str) -> None:
    """sequences.md §6: the hub retries SERIAL_PORT_BUSY only; an absent
    port is SERIAL_PORT_NOT_FOUND, which no retry would fix."""
    assert agent._serial_open_code(exc) == f"SERIAL_PORT_{code}"


@pytest.mark.parametrize("devices", [["EXAMPLE0001"], []])
async def test_bench_adb_list_matches_schema(
    devices: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_devices() -> list[str]:
        return devices

    monkeypatch.setattr(agent, "_adb_devices", fake_devices)
    monkeypatch.setattr(agent, "_find_adb_conflicts", lambda: ["vendor_adb.exe pid=4242"])
    ws = _ControlWs()
    await agent._reply_adb_list(ws, 5)
    assert len(ws.sent) == 1
    S.assert_valid("adb_list.json", ws.sent[0])
    assert ws.sent[0]["re"] == 5


async def test_bench_com_list_matches_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        agent, "_enumerate_com", lambda: [{"port": "COM3", "desc": "USB Serial Port (COM3)"}]
    )
    ws = _ControlWs()
    await agent._reply_com_list(ws, 6)
    S.assert_valid("com_list.json", ws.sent[0])
    assert ws.sent[0]["re"] == 6


# ── control frames the bench receives (framing.md §2) ────────────────

# What the bench may receive once a session is up: every verb that travels
# hub->bench, except hello_ok (the handshake's alone), plus error.
_DIRECTION = {f"{v}.json": S.SCHEMAS[f"{v}.json"].get("x-direction") for v in S.CONTROL_VERBS}
_TO_BENCH = {s for s, d in _DIRECTION.items() if d in ("hub->bench", "both")}
_SESSION_VERBS = {s[: -len(".json")] for s in _TO_BENCH} - {"hello_ok"}


def test_the_bench_takes_the_verbs_the_schema_sends_it() -> None:
    assert _SESSION_VERBS == agent._SESSION_VERBS


@pytest.mark.parametrize(
    "entry",
    S.samples(valid=True, schemas=_TO_BENCH - {"hello_ok.json"}),
    ids=lambda e: e["file"],
)
def test_bench_accepts_every_valid_sample_sent_to_it(entry: dict[str, Any]) -> None:
    frame = S.sample(entry)
    assert agent._parse_hub_frame(json.dumps(frame)) == frame
    if frame["verb"] == "open_channel":
        assert agent._open_channel_problem(frame) == ""


@pytest.mark.parametrize(
    "entry",
    S.samples(
        valid=False, schemas=(_TO_BENCH - {"hello_ok.json", "error.json"}) | {"control.json"}
    ),
    ids=lambda e: e["file"],
)
def test_bench_refuses_every_invalid_sample_sent_to_it(entry: dict[str, Any]) -> None:
    """A defect is an error frame with the code the rules give — except an
    open_channel whose cid is readable: someone on the hub waits for that
    cid, so the answer is channel_error BENCH_PARAM_REJECTED (sequences.md
    §5 rule 5)."""
    frame = S.sample(entry)
    try:
        parsed = agent._parse_hub_frame(json.dumps(frame))
    except agent._Refusal as e:
        verb = frame.get("verb") if isinstance(frame, dict) else None
        want = (
            "UNKNOWN_VERB" if isinstance(verb, str) and verb not in _SESSION_VERBS else "BAD_FRAME"
        )
        assert e.code == want
        re_id = frame.get("id") if isinstance(frame, dict) else None
        assert e.re_id == (re_id if isinstance(re_id, int) and re_id >= 0 else None)
        return
    assert parsed["verb"] == "open_channel", f"{entry['file']} was accepted"
    assert agent._open_channel_problem(parsed) != ""


def test_a_binary_message_is_a_bad_frame() -> None:
    with pytest.raises(agent._Refusal) as e:
        agent._parse_hub_frame(b"\x00\x01")
    assert e.value.code == "BAD_FRAME"


# ── the handshake (sequences.md §2) ──────────────────────────────────


class _HandshakeWs:
    def __init__(self, reply: Any) -> None:
        self.reply = reply
        self.sent: list[str] = []
        self.closed = False

    async def send(self, text: str) -> None:
        self.sent.append(text)

    async def recv(self) -> Any:
        return self.reply if isinstance(self.reply, str | bytes) else json.dumps(self.reply)

    async def close(self) -> None:
        self.closed = True


class _Args:
    agent_id = "bench-1"
    name = "bench one"


@pytest.mark.parametrize("entry", S.samples(valid=True, schemas={"hello_ok.json"}), ids=str)
async def test_bench_takes_a_valid_hello_ok(entry: dict[str, Any]) -> None:
    ws = _HandshakeWs(S.sample(entry))
    assert await agent._handshake(ws, _Args()) == 2
    S.assert_valid("hello.json", json.loads(ws.sent[0]))


_REFUSED_ANSWERS = [
    *(S.sample(e) for e in S.samples(valid=False, schemas={"hello_ok.json"})),
    {"verb": "hello_ok", "proto": 99},
    *(S.sample(e) for e in S.samples(valid=True, schemas={"error.json"})),
    {"verb": "list_adb", "id": 1},
    "not json",
    b"\x00",
]


@pytest.mark.parametrize("reply", _REFUSED_ANSWERS, ids=repr)
async def test_any_other_answer_to_hello_stops_the_bench(reply: Any) -> None:
    """Each of these would repeat on every reconnect: no retry."""
    with pytest.raises(agent._HandshakeRejected):
        await agent._handshake(_HandshakeWs(reply), _Args())


@pytest.mark.parametrize(
    ("supported", "who"),
    [({"min": 1, "max": 1}, "upgrade the hub"), ({"min": 3, "max": 4}, "upgrade the bench")],
)
def test_a_version_refusal_says_which_side_to_upgrade(supported: dict[str, int], who: str) -> None:
    text = agent._refusal_text(
        {
            "verb": "error",
            "code": "PROTO_VERSION_UNSUPPORTED",
            "message": "",
            "supported": supported,
        }
    )
    assert who in text


# ── job channel ──────────────────────────────────────────────────────

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


def _request(fields: dict[str, Any]) -> bytes:
    payload = json.dumps(fields).encode()
    return _HDR.pack(b"C", len(payload)) + payload


def _valid_event(frame: dict[str, Any]) -> None:
    S.assert_valid("job-event.json", frame)


async def test_bench_progress_and_done_match_schema() -> None:
    ws = _JobWs()
    await agent._job_progress(ws, "transfer", 65536, 131072)
    await agent._job_finish(ws, 0, "out", "", fail_code="")
    await agent._job_finish(ws, 1, "", "example error", fail_code="FLASH_FAILED")
    await agent._job_fail(ws, "bad opening frame: example", code="BENCH_PARAM_REJECTED")
    frames = ws.control_frames()
    assert [f["ev"] for f in frames] == ["progress", "done", "done", "done"]
    S.assert_valid("job-progress.json", frames[0])
    for f in frames[1:]:
        S.assert_valid("job-done.json", f)
        _valid_event(f)


async def test_a_failed_done_always_has_a_code_and_a_capped_error() -> None:
    """sequences.md §7 / §9: never an empty code on a failure; error text
    at most 4096 characters."""
    ws = _JobWs()
    await agent._job_fail(ws, "x" * 9000, code="")
    await agent._job_finish(ws, 1, "", "y" * 9000, fail_code="")
    for f in ws.control_frames():
        S.assert_valid("job-done.json", f)
        assert f["ok"] is False and f["code"] and len(f["error"]) == 4096


async def test_bench_unknown_op_answer_matches_schema() -> None:
    ws = _JobWs(_request({"op": "no-such-op"}))
    await agent._run_job(ws, {})
    (done,) = ws.control_frames()
    assert done["code"] == "BENCH_OP_UNKNOWN"
    S.assert_valid("job-done.json", done)


async def test_bench_builtin_op_answers_match_schema() -> None:
    async def probe(_spec: Any, _values: Any) -> tuple[str, dict[str, Any]]:
        return "probe output", {"answer": 42}

    spec = agent.OpSpec(
        "probe", "host", "example builtin", mutates=False, exclusive=False, builtin=probe
    )
    ws = _JobWs(_request({"op": "probe"}))
    await agent._run_job(ws, {"probe": spec}, hint="probe")
    frames = ws.control_frames()
    assert [f["ev"] for f in frames] == ["accepted", "done"]
    S.assert_valid("job-accepted.json", frames[0])
    S.assert_valid("job-done.json", frames[1])
    assert frames[1]["data"] == {"answer": 42}


async def test_bench_file_frames_match_schema(tmp_path: Path) -> None:
    (tmp_path / "run.txt").write_bytes(b"example output\n")
    (tmp_path / "empty.log").write_bytes(b"")
    ws = _JobWs()
    report = await agent._tool_send_files(ws, tmp_path, {})
    assert {r["name"] for r in report if r["sent"]} == {"run.txt", "empty.log"}
    frames = ws.control_frames()
    assert {f["name"] for f in frames} == {"run.txt", "empty.log"}
    for f in frames:
        S.assert_valid("job-file.json", f)
        _valid_event(f)


@pytest.mark.parametrize(
    "entry", S.samples(valid=True, schemas={"job-request.json"}), ids=lambda e: e["file"]
)
def test_bench_takes_every_valid_job_request(
    entry: dict[str, Any], full_table: dict[str, Any]
) -> None:
    req = S.sample(entry)
    spec = full_table[req["op"]]
    assert agent._request_stray_keys(spec, req) == []
    spec.resolve(req)


@pytest.mark.parametrize(
    "entry", S.samples(valid=False, schemas={"job-request.json"}), ids=lambda e: e["file"]
)
async def test_bench_refuses_every_invalid_job_request(
    entry: dict[str, Any], full_table: dict[str, Any]
) -> None:
    """The channel was opened for the op the request names (flash, for the
    one without an op); the answer is a failed done with a code, before any
    tool runs."""
    req = S.sample(entry)
    ws = _JobWs(_request(req))
    await agent._run_job(ws, full_table, hint=str(req.get("op", "flash")))
    (done,) = ws.control_frames()
    S.assert_valid("job-done.json", done)
    assert done["ok"] is False
    assert done["code"] == "BENCH_PARAM_REJECTED"


@pytest.mark.parametrize(
    ("req", "code"),
    [
        ({"op": "devices", "verbose": True}, "BENCH_PARAM_REJECTED"),
        ({"op": "log-tail", "source": "agent", "lines": "50"}, "BENCH_PARAM_REJECTED"),
        ({"op": "devices", "size": 1, "sha256": "ab" * 32}, "BENCH_PARAM_REJECTED"),
    ],
    ids=["stray key", "int as a string", "image keys on a non-image op"],
)
async def test_job_request_rules_of_sequences_7(
    req: dict[str, Any], code: str, full_table: dict[str, Any]
) -> None:
    ws = _JobWs(_request(req))
    await agent._run_job(ws, full_table, hint=req["op"])
    (done,) = ws.control_frames()
    assert (done["ok"], done["code"]) == (False, code)


# ── byte-exact job frames ────────────────────────────────────────────


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
    assert S.BINARY["max_payload"] == agent._JOB_MAX_FRAME


def test_the_sender_checks_the_cap_too() -> None:
    """framing.md §3.2: a payload over the cap is not sent."""
    with pytest.raises(ValueError):
        agent._job_encode(b"D", bytes(agent._JOB_MAX_FRAME + 1))


@pytest.mark.parametrize("case", S.BINARY["valid"], ids=lambda c: c["name"])
def test_bench_encoder_produces_the_golden_bytes(case: dict[str, Any]) -> None:
    want = bytes.fromhex(case["hex"])
    if case["kind"] == "C":
        assert agent._job_control(json.loads(case["payload_text"])) == want
    else:
        assert agent._job_encode(b"D", _payload(case)) == want


@pytest.mark.parametrize("case", S.BINARY["valid"], ids=lambda c: c["name"])
async def test_bench_reader_decodes_the_golden_bytes(case: dict[str, Any]) -> None:
    data = bytes.fromhex(case["hex"])
    want = (case["kind"].encode(), _payload(case))
    assert await agent._JobReader(_ByteStream(data)).read() == want


@pytest.mark.parametrize("case", S.BINARY["invalid"], ids=lambda c: c["name"])
async def test_bench_reader_rejects_the_bad_bytes(case: dict[str, Any]) -> None:
    data = bytes.fromhex(case["hex"])
    with pytest.raises(agent._JobProtocolError):
        await agent._JobReader(_ByteStream(data)).read()
