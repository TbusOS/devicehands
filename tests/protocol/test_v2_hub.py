"""The hub (devicehands.remote.protocol, jobframe, bench_ops) against protocol/v2.

What the hub sends must pass the schema; what the schema calls valid the
hub must accept, and what it calls invalid the hub must refuse — driven by
the sample index, so a new sample needs no change here. The hub decoder is
hand-written (the hub does not depend on a JSON Schema library at run time),
which is exactly why it is held to the same samples.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

pytest.importorskip("jsonschema")
pytest.importorskip("referencing")

from devicehands.remote import bench_ops, jobframe
from devicehands.remote import protocol as P
from tests.protocol import schema_v2 as S

_CID = "0123456789abcdef0123456789abcdef"
_SHA = "ab" * 32


def _hello(**kw: Any) -> dict[str, Any]:
    fields: dict[str, Any] = dict(
        bench_id="bench-1",
        name="bench one",
        lang="python",
        build="0123456789ab",
        os="windows",
        ops=[s.as_dict() for s in bench_ops.legacy_ops(["adb", "fastboot"]).values()],
        boards={"board-a": {"partitions": None, "product": "EXAMPLE-PRODUCT"}},
    )
    fields.update(kw)
    return P.hello(**fields)


# ── what the hub builds ──────────────────────────────────────────────

_CONTROL = [
    ("hello_ok.json", lambda: P.hello_ok(proto=2)),
    ("error.json", lambda: P.error(P.BAD_FRAME, "not a JSON object")),
    ("error.json", lambda: P.error(P.UNKNOWN_VERB, "unknown verb", re_id=7)),
    ("error.json", lambda: P.version_refusal(3, 4)),
    ("list_adb.json", lambda: P.list_adb(req_id=1)),
    ("list_com.json", lambda: P.list_com(req_id=2)),
    ("restart_adb.json", lambda: P.restart_adb(req_id=3)),
    ("restart_adb.json", lambda: P.restart_adb(req_id=4, kill_conflicts=True)),
    *[
        (
            "open_channel.json",
            (
                lambda ctype=ctype, params=params: P.open_channel(
                    cid=P.new_cid(), csecret=P.new_csecret(), ctype=ctype, params=params
                )
            ),
        )
        for ctype, params in (
            (P.ChannelType.TCP, {"target": P.ADB_TARGET}),
            (P.ChannelType.SERIAL, {"com": "COM3", "baud": 115200}),
            (P.ChannelType.SERIAL, {"com": "loop://", "baud": 115200}),
            (P.ChannelType.JOB, {"op": "flash"}),
        )
    ],
    # bench-direction builders, used by tests that play the bench
    ("hello.json", _hello),
    ("hello.json", lambda: _hello(ops=[], boards={}, update=None, name="")),
    (
        "hello.json",
        lambda: _hello(
            update={
                "id": "u-1",
                "outcome": "pending",
                "from_build": "ba9876543210",
                "to_build": "0123456789ab",
                "at": 1.5,
            }
        ),
    ),
    ("heartbeat.json", P.heartbeat),
    ("adb_list.json", lambda: P.adb_list(re_id=1, devices=["EXAMPLE0001"], conflicts=[])),
    ("com_list.json", lambda: P.com_list(re_id=2, ports=[{"port": "COM3", "desc": ""}])),
    (
        "channel_error.json",
        lambda: P.channel_error(cid=_CID, code="SERIAL_PORT_BUSY", reason="Access is denied"),
    ),
]


@pytest.mark.parametrize(("schema", "build"), _CONTROL, ids=[s for s, _ in _CONTROL])
def test_hub_control_builder_matches_schema(schema: str, build: Any) -> None:
    frame = build()
    S.assert_valid(schema, frame)
    S.assert_valid("control.json", frame)
    assert P.decode_control(P.encode_control(frame)) == frame


_JOB = [
    ("job-request.json", lambda: P.job_op("devices", {})),
    ("job-request.json", lambda: P.job_op("getvar", {"name": "product"})),
    ("job-request.json", lambda: P.job_op("reboot", {"target": ""})),
    (
        "job-request.json",
        lambda: P.job_op("flash", {"partition": "boot_a"}, size=4096, sha256=_SHA),
    ),
    ("job-request.json", lambda: P.job_op("log-tail", {"source": "agent", "lines": 50})),
    ("job-request.json", lambda: P.job_op("tool-run", {"tool": "example-tool", "args": ["-v"]})),
    ("job-request.json", lambda: P.job_op("boot", {}, size=4096, sha256=_SHA)),
    ("job-accepted.json", lambda: P.job_accepted(detail="devices")),
    ("job-progress.json", lambda: P.job_progress(phase="transfer", done=1, total=2)),
    ("job-progress.json", lambda: P.job_progress(phase="flash", done=0, total=0, text="x")),
    ("job-done.json", lambda: P.job_done(ok=True, rc=0)),
    ("job-done.json", lambda: P.job_done(ok=False, rc=1, error="x", code="FLASH_FAILED", data={})),
    ("job-done.json", lambda: P.job_done(ok=False, rc=1, error="x" * 9000, code="FLASH_FAILED")),
]


@pytest.mark.parametrize(("schema", "build"), _JOB, ids=[s for s, _ in _JOB])
def test_hub_job_builder_matches_schema(schema: str, build: Any) -> None:
    frame = json.loads(json.dumps(build()))
    S.assert_valid(schema, frame)
    if schema != "job-request.json":
        S.assert_valid("job-event.json", frame)
        assert P.check_job_event(frame) == ""


def test_the_hubs_op_rows_match_the_schema() -> None:
    """The rows the hub itself shows (legacy_ops, as_dict round trips) are
    well-formed OpDescs, partition rule included."""
    for spec in bench_ops.legacy_ops(["fastboot"]).values():
        row = spec.as_dict()
        S.assert_valid("op-table.json#/$defs/OpDesc", row)
        assert P.op_desc_problem(row) == ""


# ── what the hub accepts ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "entry", S.samples(valid=True, schemas=S.CONTROL_SCHEMAS), ids=lambda e: e["file"]
)
def test_hub_decoder_accepts_every_valid_control_sample(entry: dict[str, Any]) -> None:
    frame = S.sample(entry)
    assert P.decode_control(json.dumps(frame, ensure_ascii=False)) == frame


_CONTROL_INVALID = S.samples(valid=False, schemas=S.CONTROL_SCHEMAS | {"control.json"})


@pytest.mark.parametrize("entry", _CONTROL_INVALID, ids=lambda e: e["file"])
def test_hub_decoder_refuses_every_invalid_control_sample(entry: dict[str, Any]) -> None:
    frame = S.sample(entry)
    with pytest.raises(P.FrameError) as e:
        P.decode_control(json.dumps(frame, ensure_ascii=False))
    verb = frame.get("verb") if isinstance(frame, dict) else None
    want = P.UNKNOWN_VERB if isinstance(verb, str) and verb not in S.CONTROL_VERBS else P.BAD_FRAME
    assert e.value.code == want


@pytest.mark.parametrize(
    "entry",
    S.samples(valid=True, schemas={"op-table.json#/$defs/OpDesc"}),
    ids=lambda e: e["file"],
)
def test_op_desc_check_accepts_valid_rows(entry: dict[str, Any]) -> None:
    assert P.op_desc_problem(S.sample(entry)) == ""


def _as_row(param: Any) -> dict[str, Any]:
    return {
        "name": "x",
        "group": "host",
        "summary": "",
        "params": [param],
        "needs": "",
        "image": False,
        "max_image_bytes": 0,
        "mutates": False,
        "exclusive": False,
        "record": "none",
        "timeout_s": 1.0,
    }


@pytest.mark.parametrize(
    "entry",
    S.samples(valid=False, schemas={"op-table.json#/$defs/OpDesc"})
    + S.samples(valid=False, schemas={"op-table.json#/$defs/ParamDesc"}),
    ids=lambda e: e["file"],
)
def test_op_desc_check_refuses_invalid_rows(entry: dict[str, Any]) -> None:
    sample = S.sample(entry)
    row = sample if entry["schema"].endswith("OpDesc") else _as_row(sample)
    assert P.op_desc_problem(row) != ""


@pytest.mark.parametrize(
    "entry",
    S.samples(valid=True, schemas={"op-table.json#/$defs/ParamDesc"}),
    ids=lambda e: e["file"],
)
def test_op_desc_check_accepts_valid_params(entry: dict[str, Any]) -> None:
    assert P.op_desc_problem(_as_row(S.sample(entry))) == ""


@pytest.mark.parametrize(
    "entry", S.samples(valid=True, schemas=S.EVENT_SCHEMAS), ids=lambda e: e["file"]
)
def test_job_event_check_accepts_valid_events(entry: dict[str, Any]) -> None:
    assert P.check_job_event(S.sample(entry)) == ""


@pytest.mark.parametrize(
    "entry",
    S.samples(valid=False, schemas=S.EVENT_SCHEMAS | {"job-event.json"}),
    ids=lambda e: e["file"],
)
def test_job_event_check_refuses_invalid_events(entry: dict[str, Any]) -> None:
    assert P.check_job_event(S.sample(entry)) != ""


# ── names and enums ──────────────────────────────────────────────────


def test_verbs_match_the_schema() -> None:
    assert sorted(v.value for v in P.Verb) == sorted(S.CONTROL_VERBS)
    directions: dict[str, set[str]] = {
        S.SCHEMAS[f"{v}.json"]["x-direction"]: set() for v in S.CONTROL_VERBS
    }
    for v in S.CONTROL_VERBS:
        directions[S.SCHEMAS[f"{v}.json"]["x-direction"]].add(v)
    hub = {v.value for v in P.HUB_RECEIVES}
    bench = {v.value for v in P.BENCH_RECEIVES}
    assert hub == directions["bench->hub"] | directions["both"]
    assert bench == directions["hub->bench"] | directions["both"]


def test_enums_match_the_schema() -> None:
    oc = S.SCHEMAS["open_channel.json"]["properties"]
    assert sorted(t.value for t in P.ChannelType) == sorted(oc["channel_type"]["enum"])
    assert {e.value for e in P.JobEvent} == S.JOB_EVENTS
    op = S.SCHEMAS["op-table.json"]["$defs"]["OpDesc"]["properties"]
    param = S.SCHEMAS["op-table.json"]["$defs"]["ParamDesc"]["properties"]
    assert list(P.OP_GROUPS) == op["group"]["enum"]
    assert list(P.OP_NEEDS) == op["needs"]["enum"]
    assert list(P.RECORD_TIERS) == op["record"]["enum"]
    assert list(P.PARAM_KINDS) == param["kind"]["enum"]
    assert bench_ops.KINDS == P.PARAM_KINDS


def test_the_partition_rule_is_the_one_in_the_samples() -> None:
    flash = S.sample({"file": "valid/op-desc-flash.json"})
    (partition,) = flash["params"]
    assert partition["pattern"] == bench_ops.PARTITION_PATTERN
    assert partition["max_len"] == bench_ops.PARTITION_MAX_LEN


def test_paths_and_headers() -> None:
    assert (P.CONNECT_PATH, P.CHANNEL_PATH) == ("/bench/connect", "/bench/channel")
    assert (P.TOKEN_HEADER, P.CSECRET_HEADER) == ("x-dhands-token", "x-dhands-csecret")
    tcp = S.SCHEMAS["open_channel.json"]["allOf"][0]["then"]["properties"]["params"]
    assert tcp["properties"]["target"]["const"] == P.ADB_TARGET


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


def test_binary_cap_matches_the_hub() -> None:
    assert S.BINARY["max_payload"] == jobframe.MAX_FRAME


@pytest.mark.parametrize("case", S.BINARY["valid"], ids=lambda c: c["name"])
def test_hub_encoder_produces_the_golden_bytes(case: dict[str, Any]) -> None:
    want = bytes.fromhex(case["hex"])
    if case["kind"] == "C":
        assert jobframe.encode_control(json.loads(case["payload_text"])) == want
    else:
        assert jobframe.encode_data(_payload(case)) == want


@pytest.mark.parametrize("case", S.BINARY["valid"], ids=lambda c: c["name"])
async def test_hub_reader_decodes_the_golden_bytes(case: dict[str, Any]) -> None:
    data = bytes.fromhex(case["hex"])
    assert await jobframe.FrameReader(_ByteStream(data)).read() == (
        case["kind"].encode(),
        _payload(case),
    )


@pytest.mark.parametrize("case", S.BINARY["invalid"], ids=lambda c: c["name"])
async def test_hub_reader_rejects_the_bad_bytes(case: dict[str, Any]) -> None:
    with pytest.raises(jobframe.JobProtocolError):
        await jobframe.FrameReader(_ByteStream(bytes.fromhex(case["hex"]))).read()
