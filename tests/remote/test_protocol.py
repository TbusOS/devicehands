"""Wire-protocol unit tests, protocol v2 (devicehands.remote.protocol).

The schema-level checks (every builder against protocol/v2/schema, every
sample frame through the decoder) are in tests/protocol/test_v2_hub.py;
these pin the behaviour around them: version negotiation, which code an
incoming frame is refused with, and the request numbering.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from devicehands.remote import protocol as p
from devicehands.remote.protocol import (
    BAD_FRAME,
    UNKNOWN_VERB,
    ChannelType,
    FrameError,
    Verb,
    decode_control,
    encode_control,
    new_cid,
    new_csecret,
)


def _roundtrip(msg: dict[str, Any]) -> dict[str, Any]:
    return decode_control(encode_control(msg))


def _hello(**kw: Any) -> dict[str, Any]:
    fields: dict[str, Any] = dict(
        bench_id="bench-1",
        name="bench one",
        lang="python",
        build="0123456789ab",
        os="linux",
        ops=[],
        boards={},
    )
    fields.update(kw)
    return p.hello(**fields)


def test_every_builder_roundtrips_and_carries_no_version_field() -> None:
    cid = new_cid()
    msgs = [
        _hello(),
        p.hello_ok(proto=2),
        p.error(BAD_FRAME, "x", re_id=3),
        p.version_refusal(3, 3),
        p.heartbeat(),
        p.list_adb(req_id=1),
        p.adb_list(re_id=1, devices=["serial1"], conflicts=[]),
        p.list_com(req_id=2),
        p.com_list(re_id=2, ports=[{"port": "COM3", "desc": ""}]),
        p.restart_adb(req_id=3, kill_conflicts=True),
        p.open_channel(
            cid=cid, csecret=new_csecret(), ctype=ChannelType.TCP, params={"target": p.ADB_TARGET}
        ),
        p.channel_error(cid=cid, code="SERIAL_PORT_BUSY", reason="boom"),
    ]
    for m in msgs:
        out = _roundtrip(m)
        assert out == m
        assert "v" not in out
        assert out["verb"] in {v.value for v in Verb}


def test_open_channel_carries_no_role() -> None:
    m = p.open_channel(
        cid=new_cid(),
        csecret="s" * 43,
        ctype=ChannelType.SERIAL,
        params={"com": "COM7", "baud": 1500000},
    )
    assert "role" not in m
    assert m["channel_type"] == "serial" and m["params"]["baud"] == 1500000


@pytest.mark.parametrize(
    ("bench", "chosen"),
    [((2, 2), 2), ((1, 2), 2), ((2, 5), 2), ((1, 1), None), ((3, 4), None)],
)
def test_negotiate_takes_the_largest_common_version(
    bench: tuple[int, int], chosen: int | None
) -> None:
    assert p.negotiate(*bench) == chosen


def test_version_refusal_names_the_side_to_upgrade() -> None:
    older = p.version_refusal(1, 1)
    newer = p.version_refusal(3, 3)
    assert older["code"] == newer["code"] == p.PROTO_VERSION_UNSUPPORTED
    assert older["supported"] == {"min": p.PROTO_MIN, "max": p.PROTO_MAX}
    assert "upgrade the bench" in older["message"]
    assert "upgrade the hub" in newer["message"]


@pytest.mark.parametrize(
    ("raw", "code", "re_id"),
    [
        ("not json {", BAD_FRAME, None),
        ("[1, 2, 3]", BAD_FRAME, None),
        (b"\x00binary", BAD_FRAME, None),
        ('{"id": 4}', BAD_FRAME, 4),
        ('{"verb": "teleport", "id": 5}', UNKNOWN_VERB, 5),
        ('{"verb": "channel_opened", "cid": "x"}', UNKNOWN_VERB, None),
        ('{"verb": "list_com"}', BAD_FRAME, None),
        ('{"verb": "list_com", "id": "1"}', BAD_FRAME, None),
        ('{"verb": "list_com", "id": true}', BAD_FRAME, None),
        ('{"verb": "restart_adb", "id": 6, "kill_conflicts": 1}', BAD_FRAME, 6),
        ('{"verb": "hello_ok", "proto": 2, "id": -1}', None, None),
    ],
)
def test_decode_refuses_with_the_code_the_answer_needs(
    raw: str | bytes, code: str | None, re_id: int | None
) -> None:
    if code is None:
        assert decode_control(raw)["proto"] == 2  # a negative id is just unread
        return
    with pytest.raises(FrameError) as e:
        decode_control(raw)
    assert (e.value.code, e.value.re_id) == (code, re_id)


def test_decode_ignores_unknown_fields() -> None:
    assert decode_control('{"verb": "heartbeat", "later": {"x": 1}}')["later"] == {"x": 1}


def test_a_verb_for_the_other_side_is_unknown_to_this_one() -> None:
    with pytest.raises(FrameError) as e:
        decode_control(encode_control(p.hello_ok(proto=2)), accept=p.HUB_RECEIVES)
    assert e.value.code == UNKNOWN_VERB
    assert decode_control(encode_control(p.heartbeat()), accept=p.HUB_RECEIVES)


def test_hello_with_an_inverted_range_is_a_bad_frame() -> None:
    hello = _hello()
    hello["proto"] = {"min": 3, "max": 2}
    with pytest.raises(FrameError) as e:
        decode_control(json.dumps(hello))
    assert e.value.code == BAD_FRAME


def test_hello_with_a_pattern_outside_the_subset_is_a_bad_frame() -> None:
    row: dict[str, Any] = {
        "name": "getvar",
        "group": "fastboot",
        "summary": "",
        "params": [
            {
                "name": "name",
                "kind": "text",
                "required": False,
                "default": "all",
                "pattern": "^[A-Za-z0-9]{1,64}\\Z",
                "max_len": 64,
            }
        ],
        "needs": "fastboot-device",
        "image": False,
        "max_image_bytes": 0,
        "mutates": False,
        "exclusive": True,
        "record": "none",
        "timeout_s": 20.0,
    }
    with pytest.raises(FrameError) as e:
        decode_control(json.dumps(_hello(ops=[row])))
    assert e.value.code == BAD_FRAME and "portable subset" in e.value.message
    row["params"][0]["pattern"] = "[A-Za-z0-9]{1,64}"
    assert decode_control(json.dumps(_hello(ops=[row])))["ops"][0]["name"] == "getvar"


def test_hello_with_a_duplicate_op_is_a_bad_frame() -> None:
    row = json.loads(json.dumps(_OP_DEVICES))
    with pytest.raises(FrameError):
        decode_control(json.dumps(_hello(ops=[row, row])))


_OP_DEVICES = {
    "name": "devices",
    "group": "fastboot",
    "summary": "fastboot devices",
    "params": [],
    "needs": "",
    "image": False,
    "max_image_bytes": 0,
    "mutates": False,
    "exclusive": True,
    "record": "none",
    "timeout_s": 20.0,
}


def test_job_done_cuts_error_and_output_to_the_limits() -> None:
    done = p.job_done(
        ok=False,
        rc=1,
        stdout="o" * 70000,
        stderr="e" * 70000,
        error="x" * 5000,
        code="FLASH_FAILED",
    )
    assert len(done["error"]) == p.MAX_ERROR_TEXT
    assert len(done["stdout"]) == len(done["stderr"]) == p.MAX_OUTPUT_TEXT
    assert p.check_job_event(done) == ""


@pytest.mark.parametrize(
    ("event", "fine"),
    [
        (p.job_accepted(detail="x"), True),
        (p.job_progress(phase="verify", done=1, total=2), True),
        (p.job_done(ok=True, rc=0), True),
        (p.job_done(ok=False, rc=1, error="x", code="FLASH_FAILED"), True),
        (p.job_done(ok=False, rc=1, error="x", code=""), False),
        ({"ev": "done", "ok": True, "rc": 0}, False),
        ({"ev": "log", "text": "x"}, False),
        (p.job_progress(phase="Flash", done=0, total=0), False),
    ],
)
def test_check_job_event(event: dict[str, Any], fine: bool) -> None:
    assert (p.check_job_event(event) == "") is fine


def test_new_cid_unique_and_hex() -> None:
    cids = {new_cid() for _ in range(1000)}
    assert len(cids) == 1000
    assert all(len(c) == 32 and all(ch in "0123456789abcdef" for ch in c) for c in cids)


def test_new_csecret_unique_and_high_entropy() -> None:
    secs = {new_csecret() for _ in range(1000)}
    assert len(secs) == 1000  # no collisions
    # token_urlsafe(32) → 32 bytes of entropy, 43 url-safe chars
    assert all(len(s) == 43 for s in secs)
