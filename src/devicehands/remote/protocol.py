"""Wire protocol between the hub and a bench, version 2 (protocol/v2).

Two kinds of connection, both dialled by the bench:

    /bench/connect           signaling: JSON control frames (this module)
    /bench/channel?cid=...   one data connection per open_channel: raw bytes
                             for tcp / serial, job frames (jobframe) for job

The language-neutral description is protocol/v2 (schema, sample frames,
framing.md, sequences.md); tests/protocol checks this module against it.
What lives here:

- names: paths, credential headers, verbs, error codes, channel types;
- builders for every control frame the hub sends (and, for tests, the ones
  the bench sends);
- `decode_control`: parse and check one incoming frame against the v2
  rules, raising `FrameError` with the code the answer must carry;
- `negotiate`: pick the session's version from the bench's range;
- `check_job_event`: the same for job-channel events.

There is no compatibility with v1: the hub speaks v2 only
(protocol/v2/DESIGN.md section 2).
"""

from __future__ import annotations

import enum
import json
import math
import re
import secrets
import uuid
from typing import Any

from devicehands.remote import rule_pattern

# Versions this hub speaks (sequences.md section 2). The hub keeps the
# previous version as well once there is one; v2 is the first with a range.
PROTO_MIN = 2
PROTO_MAX = 2

CONNECT_PATH = "/bench/connect"
CHANNEL_PATH = "/bench/channel"
# Credentials ride headers only — never a frame, never the query string — so
# frames can be logged whole and request lines leak nothing (ADR-055).
TOKEN_HEADER = "x-dhands-token"
CSECRET_HEADER = "x-dhands-csecret"

# The tcp channel target: names "the bench host's adb server". The bench
# maps it to its configured port and refuses anything else, as does the
# hub, so a bench never becomes an open proxy (ADR-050 §6).
ADB_TARGET = "127.0.0.1:5037"

# Protocol-level error codes; they appear only in `error` frames.
PROTO_VERSION_UNSUPPORTED = "PROTO_VERSION_UNSUPPORTED"
UNKNOWN_VERB = "UNKNOWN_VERB"
BAD_FRAME = "BAD_FRAME"

# Limits (sequences.md section 9).
HELLO_TIMEOUT_S = 10.0
HEARTBEAT_INTERVAL_S = 20.0
HEARTBEAT_TIMEOUT_S = 60.0
MAX_ERROR_TEXT = 4096
MAX_OUTPUT_TEXT = 65536


class ProtocolError(Exception):
    """A control frame cannot be acted on."""


class FrameError(ProtocolError):
    """An incoming frame broke a v2 rule. `code` is what the `error` answer
    carries; `re` is the frame's id when it had a readable one."""

    def __init__(self, code: str, message: str, re_id: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.re_id = re_id


class ChannelType(str, enum.Enum):
    # The retry rule follows from the type (sequences.md section 6): tcp
    # proxies a daemon and fails fast; serial proxies an exclusive port and
    # may retry a busy one; a job may already have changed the device, so a
    # second attempt would be a second write.
    TCP = "tcp"
    SERIAL = "serial"
    JOB = "job"


class Verb(str, enum.Enum):
    HELLO = "hello"
    HELLO_OK = "hello_ok"
    ERROR = "error"
    HEARTBEAT = "heartbeat"
    LIST_ADB = "list_adb"
    ADB_LIST = "adb_list"
    LIST_COM = "list_com"
    COM_LIST = "com_list"
    RESTART_ADB = "restart_adb"
    OPEN_CHANNEL = "open_channel"
    CHANNEL_ERROR = "channel_error"


# Which verbs each side may receive. Anything else — unknown, or a verb that
# only travels the other way — is UNKNOWN_VERB (framing.md section 2).
HUB_RECEIVES = frozenset(
    {Verb.HELLO, Verb.ERROR, Verb.HEARTBEAT, Verb.ADB_LIST, Verb.COM_LIST, Verb.CHANNEL_ERROR}
)
BENCH_RECEIVES = frozenset(
    {
        Verb.HELLO_OK,
        Verb.ERROR,
        Verb.LIST_ADB,
        Verb.LIST_COM,
        Verb.RESTART_ADB,
        Verb.OPEN_CHANNEL,
    }
)


class JobEvent(enum.StrEnum):
    """Bench -> hub events on a job channel. A closed set: any other `ev`
    is a broken channel. Progress phases are open (`phase` is any short
    lowercase name), so there is no phase enum."""

    ACCEPTED = "accepted"
    PROGRESS = "progress"
    FILE = "file"
    DONE = "done"


def new_cid() -> str:
    """The routing key of one data channel: 32 lowercase hex digits."""
    return uuid.uuid4().hex


def new_csecret() -> str:
    """The per-channel secret (DEBT-084): sent only on the owning bench's
    signaling connection, presented back in CSECRET_HEADER on the dial-back,
    compared in constant time. 43 base64url characters."""
    return secrets.token_urlsafe(32)


def negotiate(bench_min: int, bench_max: int) -> int | None:
    """The largest version both sides speak, or None."""
    top = min(PROTO_MAX, bench_max)
    return top if top >= max(PROTO_MIN, bench_min) else None


# ── builders ─────────────────────────────────────────────────────────


def _frame(verb: Verb, **fields: Any) -> dict[str, Any]:
    return {"verb": verb.value, **fields}


def hello(
    *,
    bench_id: str,
    name: str,
    lang: str,
    build: str,
    os: str,
    ops: list[dict[str, Any]],
    boards: dict[str, Any],
    update: dict[str, Any] | None = None,
    proto_min: int = PROTO_MIN,
    proto_max: int = PROTO_MAX,
) -> dict[str, Any]:
    """What a bench sends first. The hub never sends one; tests build it."""
    msg = _frame(
        Verb.HELLO,
        proto={"min": proto_min, "max": proto_max},
        bench_id=bench_id,
        name=name,
        impl={"lang": lang, "build": build},
        os=os,
        ops=ops,
        boards=boards,
    )
    if update is not None:
        msg["update"] = update
    return msg


def hello_ok(*, proto: int) -> dict[str, Any]:
    return _frame(Verb.HELLO_OK, proto=proto)


def error(
    code: str,
    message: str,
    *,
    re_id: int | None = None,
    supported: tuple[int, int] | None = None,
) -> dict[str, Any]:
    msg = _frame(Verb.ERROR, code=code, message=message[:MAX_ERROR_TEXT])
    if re_id is not None:
        msg["re"] = re_id
    if supported is not None:
        msg["supported"] = {"min": supported[0], "max": supported[1]}
    return msg


def version_refusal(bench_min: int, bench_max: int) -> dict[str, Any]:
    """The answer to a hello whose range misses ours, saying which side is behind."""
    if bench_max < PROTO_MIN:
        who = "the bench is older than this hub — upgrade the bench program"
    else:
        who = "the bench is newer than this hub — upgrade the hub"
    return error(
        PROTO_VERSION_UNSUPPORTED,
        f"this hub speaks protocol {PROTO_MIN}..{PROTO_MAX}, the bench "
        f"{bench_min}..{bench_max}: {who}",
        supported=(PROTO_MIN, PROTO_MAX),
    )


def heartbeat() -> dict[str, Any]:
    return _frame(Verb.HEARTBEAT)


def list_adb(*, req_id: int) -> dict[str, Any]:
    return _frame(Verb.LIST_ADB, id=req_id)


def adb_list(*, re_id: int, devices: list[str], conflicts: list[str]) -> dict[str, Any]:
    return _frame(Verb.ADB_LIST, re=re_id, devices=devices, conflicts=conflicts)


def restart_adb(*, req_id: int, kill_conflicts: bool = False) -> dict[str, Any]:
    """Ask the bench to restart ITS local adb server, then re-report devices.

    kill_conflicts asks the bench to first terminate adb-flavoured foreign
    processes (renamed vendor adb builds hold the exclusive USB interface).
    Deliberately a bool: WHAT matches is the bench's own heuristic — the hub
    can never name a process to kill."""
    return _frame(Verb.RESTART_ADB, id=req_id, kill_conflicts=kill_conflicts)


def list_com(*, req_id: int) -> dict[str, Any]:
    return _frame(Verb.LIST_COM, id=req_id)


def com_list(*, re_id: int, ports: list[dict[str, str]]) -> dict[str, Any]:
    return _frame(Verb.COM_LIST, re=re_id, ports=ports)


def open_channel(
    *, cid: str, csecret: str, ctype: ChannelType, params: dict[str, Any]
) -> dict[str, Any]:
    return _frame(
        Verb.OPEN_CHANNEL, cid=cid, csecret=csecret, channel_type=ctype.value, params=params
    )


def channel_error(*, cid: str, code: str, reason: str) -> dict[str, Any]:
    return _frame(Verb.CHANNEL_ERROR, cid=cid, code=code, reason=reason)


# ── codec ────────────────────────────────────────────────────────────


def encode_control(msg: dict[str, Any]) -> str:
    """Serialize a control frame to JSON text."""
    return json.dumps(msg, ensure_ascii=False)


# Fixed sets in the op table (op-table.json). Adding a value is a version
# change; op names and progress phases are the open sets.
OP_GROUPS = ("fastboot", "host", "edl", "tool")
OP_NEEDS = ("", "fastboot-device", "edl-device")
PARAM_KINDS = ("partition", "enum", "int", "text", "argv")
RECORD_TIERS = ("none", "events", "uart")

_CID_RE = re.compile(r"[0-9a-f]{32}")
_CSECRET_RE = re.compile(r"[A-Za-z0-9_-]{43}")
_CODE_RE = re.compile(r"[A-Z][A-Z0-9_]*")
_OP_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_BUILD_RE = re.compile(r"[0-9A-Za-z._+-]{0,64}")
BOARD_NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_PHASE_RE = re.compile(r"[a-z][a-z0-9_-]{0,31}")
_B64_RE = re.compile(r"[A-Za-z0-9+/]*={0,2}")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _id_of(obj: dict[str, Any]) -> int | None:
    value = obj.get("id")
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


class _Check:
    """Field checks for one frame; the first failure raises BAD_FRAME."""

    def __init__(self, obj: dict[str, Any], where: str, re_id: int | None) -> None:
        self.obj = obj
        self.where = where
        self.re_id = re_id

    def fail(self, why: str) -> FrameError:
        return FrameError(BAD_FRAME, f"{self.where}: {why}", self.re_id)

    def need(self, key: str) -> Any:
        if key not in self.obj:
            raise self.fail(f"missing {key!r}")
        return self.obj[key]

    def string(self, key: str, *, pattern: re.Pattern[str] | None = None, max_len: int = 0) -> str:
        value = self.need(key)
        if not isinstance(value, str):
            raise self.fail(f"{key!r} must be a string")
        if max_len and len(value) > max_len:
            raise self.fail(f"{key!r} is longer than {max_len} characters")
        if pattern is not None and not pattern.fullmatch(value):
            raise self.fail(f"{key!r} has the wrong shape")
        return value

    def integer(self, key: str, *, minimum: int | None = None) -> int:
        value = self.need(key)
        if not _is_int(value) or (minimum is not None and value < minimum):
            raise self.fail(f"{key!r} must be an integer" + (f" >= {minimum}" if minimum else ""))
        return int(value)

    def boolean(self, key: str) -> bool:
        value = self.need(key)
        if not isinstance(value, bool):
            raise self.fail(f"{key!r} must be true or false")
        return value

    def obj_of(self, key: str) -> dict[str, Any]:
        value = self.need(key)
        if not isinstance(value, dict):
            raise self.fail(f"{key!r} must be an object")
        return value

    def strings(self, key: str) -> list[str]:
        value = self.need(key)
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise self.fail(f"{key!r} must be a list of strings")
        return value

    def sub(self, key: str) -> _Check:
        return _Check(self.obj_of(key), f"{self.where}.{key}", self.re_id)


def _check_range(c: _Check) -> tuple[int, int]:
    lo, hi = c.integer("min", minimum=1), c.integer("max", minimum=1)
    if lo > hi:
        raise c.fail("min is greater than max")
    return lo, hi


def _check_hello(c: _Check) -> None:
    _check_range(c.sub("proto"))
    bench_id = c.string("bench_id", max_len=128)
    if not bench_id.strip():
        raise c.fail("'bench_id' is blank")
    c.string("name", max_len=128)
    impl = c.sub("impl")
    if impl.string("lang") not in ("python", "rust"):
        raise impl.fail("'lang' must be python or rust")
    impl.string("build", pattern=_BUILD_RE)
    if c.string("os") not in ("windows", "macos", "linux"):
        raise c.fail("'os' must be windows, macos or linux")
    ops = c.need("ops")
    if not isinstance(ops, list):
        raise c.fail("'ops' must be a list")
    seen: set[str] = set()
    for i, row in enumerate(ops):
        problem = op_desc_problem(row)
        if problem:
            raise c.fail(f"ops[{i}]: {problem}")
        if row["name"] in seen:
            raise c.fail(f"ops[{i}]: op {row['name']!r} listed twice")
        seen.add(row["name"])
    boards = c.obj_of("boards")
    for board, entry in boards.items():
        if not BOARD_NAME_RE.fullmatch(board):
            raise c.fail(f"board name {board!r} has the wrong shape")
        b = _Check(entry if isinstance(entry, dict) else {}, f"boards.{board}", c.re_id)
        if not isinstance(entry, dict):
            raise b.fail("must be an object")
        parts = b.need("partitions")
        if parts is not None and (
            not isinstance(parts, list) or not all(isinstance(p, str) for p in parts)
        ):
            raise b.fail("'partitions' must be null or a list of strings")
        product = b.need("product")
        if product is not None and not isinstance(product, str):
            raise b.fail("'product' must be null or a string")
    if "update" in c.obj:
        u = c.sub("update")
        u.string("id")
        if u.string("outcome") not in ("pending", "updated", "rolled_back", "aborted"):
            raise u.fail("unknown 'outcome'")
        u.string("from_build", pattern=_BUILD_RE)
        u.string("to_build", pattern=_BUILD_RE)
        if not _is_number(u.need("at")):
            raise u.fail("'at' must be a number")


def _check_error(c: _Check) -> None:
    code = c.string("code", pattern=_CODE_RE)
    c.string("message", max_len=MAX_ERROR_TEXT)
    if "re" in c.obj:
        c.integer("re", minimum=0)
    if code == PROTO_VERSION_UNSUPPORTED or "supported" in c.obj:
        _check_range(c.sub("supported"))


def _check_param_desc(c: _Check) -> None:
    name = c.string("name", pattern=_OP_NAME_RE)
    if name == "image":
        raise c.fail("'image' is a reserved parameter name")
    kind = c.string("kind")
    if kind not in PARAM_KINDS:
        raise c.fail(f"unknown kind {kind!r}")
    c.boolean("required")
    c.need("default")
    if (kind == "enum" or "choices" in c.obj) and not c.strings("choices"):
        raise c.fail("'choices' is empty")
    if kind == "int" or "min" in c.obj or "max" in c.obj:
        c.integer("min")
        c.integer("max")
    if kind in ("text", "partition") or "pattern" in c.obj:
        problem = rule_pattern.problem(c.string("pattern"))
        if problem:
            raise c.fail(f"'pattern' is outside the portable subset: {problem}")
    if kind in ("text", "partition", "argv") or "max_len" in c.obj:
        c.integer("max_len", minimum=1)
    if kind == "argv" or "max_items" in c.obj:
        c.integer("max_items", minimum=1)


def op_desc_problem(row: Any) -> str:
    """Why one op-table row breaks op-table.json, or "" when it is fine."""
    if not isinstance(row, dict):
        return "an op must be an object"
    c = _Check(row, f"op {str(row.get('name'))[:64]!r}", None)
    try:
        c.string("name", pattern=_OP_NAME_RE)
        if c.string("group") not in OP_GROUPS:
            raise c.fail("unknown 'group'")
        c.string("summary")
        params = c.need("params")
        if not isinstance(params, list):
            raise c.fail("'params' must be a list")
        names: set[str] = set()
        for i, p in enumerate(params):
            if not isinstance(p, dict):
                raise c.fail(f"params[{i}] must be an object")
            _check_param_desc(_Check(p, f"{c.where}.params[{i}]", None))
            if p["name"] in names:
                raise c.fail(f"parameter {p['name']!r} listed twice")
            names.add(p["name"])
        if c.string("needs") not in OP_NEEDS:
            raise c.fail("unknown 'needs'")
        c.boolean("image")
        c.integer("max_image_bytes", minimum=0)
        c.boolean("mutates")
        c.boolean("exclusive")
        if c.string("record") not in RECORD_TIERS:
            raise c.fail("unknown 'record'")
        timeout = c.need("timeout_s")
        if not _is_number(timeout) or not math.isfinite(timeout) or timeout < 0:
            raise c.fail("'timeout_s' must be a number >= 0")
    except FrameError as e:
        return e.message
    return ""


def _check_open_channel(c: _Check) -> None:
    c.string("cid", pattern=_CID_RE)
    c.string("csecret", pattern=_CSECRET_RE)
    ctype = c.string("channel_type")
    p = c.sub("params")
    if ctype == ChannelType.TCP.value:
        if p.string("target") != ADB_TARGET:
            raise p.fail(f"'target' must be {ADB_TARGET}")
    elif ctype == ChannelType.SERIAL.value:
        if not p.string("com"):
            raise p.fail("'com' is empty")
        p.integer("baud", minimum=1)
    elif ctype == ChannelType.JOB.value:
        p.string("op", pattern=_OP_NAME_RE)
    else:
        raise c.fail(f"unknown channel_type {ctype!r}")


def _check_fields(verb: Verb, c: _Check) -> None:
    if verb is Verb.HELLO:
        _check_hello(c)
    elif verb is Verb.HELLO_OK:
        c.integer("proto", minimum=1)
    elif verb is Verb.ERROR:
        _check_error(c)
    elif verb in (Verb.LIST_ADB, Verb.LIST_COM):
        c.integer("id", minimum=0)
    elif verb is Verb.RESTART_ADB:
        c.integer("id", minimum=0)
        c.boolean("kill_conflicts")
    elif verb is Verb.ADB_LIST:
        c.integer("re", minimum=0)
        c.strings("devices")
        c.strings("conflicts")
    elif verb is Verb.COM_LIST:
        c.integer("re", minimum=0)
        ports = c.need("ports")
        if not isinstance(ports, list):
            raise c.fail("'ports' must be a list")
        for i, port in enumerate(ports):
            if not isinstance(port, dict):
                raise c.fail(f"ports[{i}] must be an object")
            pc = _Check(port, f"{c.where}.ports[{i}]", c.re_id)
            pc.string("port")
            pc.string("desc")
    elif verb is Verb.OPEN_CHANNEL:
        _check_open_channel(c)
    elif verb is Verb.CHANNEL_ERROR:
        c.string("cid", pattern=_CID_RE)
        c.string("code", pattern=_CODE_RE)
        c.string("reason")
    # HEARTBEAT has no fields.


def decode_control(text: str | bytes, *, accept: frozenset[Verb] | None = None) -> dict[str, Any]:
    """Parse and check one signaling message against the v2 rules.

    Raises FrameError carrying the code the answer must use: BAD_FRAME for a
    binary message, a non-object, a missing or mistyped field; UNKNOWN_VERB
    for a verb that is not in `accept` (default: every v2 verb). Unknown
    fields are left in place and ignored by the callers."""
    if not isinstance(text, str):
        raise FrameError(BAD_FRAME, "binary message on the signaling connection")
    try:
        obj = json.loads(text)
    except ValueError as e:
        raise FrameError(BAD_FRAME, f"not valid JSON: {e}") from e
    if not isinstance(obj, dict):
        raise FrameError(BAD_FRAME, "a control frame must be a JSON object")
    re_id = _id_of(obj)
    raw_verb = obj.get("verb")
    if not isinstance(raw_verb, str):
        raise FrameError(BAD_FRAME, "missing 'verb'", re_id)
    try:
        verb = Verb(raw_verb)
    except ValueError:
        raise FrameError(UNKNOWN_VERB, f"unknown verb {raw_verb[:64]!r}", re_id) from None
    if accept is not None and verb not in accept:
        raise FrameError(UNKNOWN_VERB, f"verb {verb.value!r} is not sent to this side", re_id)
    _check_fields(verb, _Check(obj, verb.value, re_id))
    return obj


# ── job channel messages ─────────────────────────────────────────────
#
# These ride the job channel's frames (devicehands.remote.jobframe), not the
# signaling connection.


def job_op(
    name: str, params: dict[str, Any], *, size: int | None = None, sha256: str = ""
) -> dict[str, Any]:
    """Opening frame of a job: flat `{op, <param>: value, ...}`. Image ops
    add `size` / `sha256` up front so the bench can refuse a corrupt transfer
    before it touches the device (ADR-056 §决定 6). No path and no command
    line ever rides it: the bench builds its own argv (ADR-056 §决定 5)."""
    msg: dict[str, Any] = {"op": name, **params}
    if size is not None:
        msg["size"] = size
        msg["sha256"] = sha256
    return msg


def job_accepted(*, detail: str = "") -> dict[str, Any]:
    return {"ev": JobEvent.ACCEPTED.value, "detail": detail}


def job_progress(*, phase: str, done: int, total: int, text: str = "") -> dict[str, Any]:
    """`total` may be 0 when the bench cannot know it — renderers must treat
    0 as "indeterminate" rather than dividing by it."""
    return {
        "ev": JobEvent.PROGRESS.value,
        "phase": phase,
        "done": done,
        "total": total,
        "text": text,
    }


def job_done(
    *,
    ok: bool,
    rc: int,
    stdout: str = "",
    stderr: str = "",
    error: str = "",
    code: str = "",
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Terminal frame. A failure always names its code (sequences.md §7)."""
    msg: dict[str, Any] = {
        "ev": JobEvent.DONE.value,
        "ok": ok,
        "rc": rc,
        "stdout": stdout[-MAX_OUTPUT_TEXT:],
        "stderr": stderr[-MAX_OUTPUT_TEXT:],
        "error": error[:MAX_ERROR_TEXT],
        "code": code,
    }
    if data is not None:
        msg["data"] = data
    return msg


def check_job_event(msg: dict[str, Any]) -> str:
    """Why a job event breaks the v2 rules, or "" when it is fine."""
    ev = msg.get("ev")
    c = _Check(msg, f"job event {ev!r}", None)
    try:
        if ev == JobEvent.ACCEPTED.value:
            c.string("detail")
        elif ev == JobEvent.PROGRESS.value:
            c.string("phase", pattern=_PHASE_RE)
            c.integer("done", minimum=0)
            c.integer("total", minimum=0)
            c.string("text")
        elif ev == JobEvent.FILE.value:
            if not c.string("name"):
                raise c.fail("'name' is empty")
            c.integer("size", minimum=0)
            c.integer("offset", minimum=0)
            c.string("b64", pattern=_B64_RE)
            c.boolean("end")
        elif ev == JobEvent.DONE.value:
            ok = c.boolean("ok")
            c.integer("rc")
            c.string("stdout", max_len=MAX_OUTPUT_TEXT)
            c.string("stderr", max_len=MAX_OUTPUT_TEXT)
            err = c.string("error", max_len=MAX_ERROR_TEXT)
            code = c.string("code")
            if ok and (code or err):
                raise c.fail("a successful done carries no code and no error")
            if not ok and not _CODE_RE.fullmatch(code):
                raise c.fail("a failed done must name its error code")
            if "data" in msg and not isinstance(msg["data"], dict):
                raise c.fail("'data' must be an object")
        else:
            return f"unknown job event {str(ev)[:64]!r}"
    except FrameError as e:
        return e.message
    return ""
