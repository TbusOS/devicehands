"""Workspace path helpers.

Convention: all artifacts land under `workspace/devices/<serial>/<category>/`,
with the serial made safe for file names by :func:`device_dir_name`.
See docs/architecture.md §四 for the full scheme.

M0 skeleton; full implementation in M1.
"""

from __future__ import annotations

import os
import re
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from devicehands.infra.dirs import default_workspace_dir

# session_id factory format: `<YYYYMMDD>-<8 hex>` (see agent/session.new_session_id).
# The pattern is intentionally tighter than the factory so we reject any
# user-supplied id that could escape the sessions/ root via `..` / absolute
# paths / unicode trickery. See L-035 (path-traversal hardening).
_SAFE_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")

# device serial shape: adb serial / ssh host / serial port basename.  Mirrors
# the conservative shape from `api/uart_stream_route._DEVICE_SAFE_RE` (the
# original N=1 site). Shared here so `workspace_path` can enforce L-035 at
# root layer for the `device=` keyword (a known user-input vector via
# `--device` CLI flag and `?device=` query params).
# Leading char must be alnum so we reject `.` / `..` / `.foo` even though
# the remaining char class allows `.` (for IP-style serials like 192.168.x.x).
_SAFE_DEVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}\Z")


class InvalidSessionId(ValueError):
    """Raised when a user-supplied session_id would escape `sessions/`."""


class InvalidDeviceSerial(ValueError):
    """Raised when a user-supplied device serial would escape the workspace."""


# profile name shape: same conservative class as session_id but tighter
# leading-char rule (must alnum). Profile names appear in `--profile <name>`
# CLI flag and `DHANDS_PROFILE` env; `profile_path(name)` builds
# `workspace/profiles/<name>.toml` so a `../etc` name would land
# `workspace/etc.toml` and load attacker-planted TOML as dhands config.
_SAFE_PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")


class InvalidProfileName(ValueError):
    """Raised when a user-supplied profile name would escape `profiles/`."""


def is_safe_session_id(s: object) -> bool:
    """True if `s` is a safe session_id (no traversal). See `session_path`.

    Accepts any object — non-str input returns False so callers can pass
    raw JSON / dict-get values without an extra isinstance check.

    A Windows reserved device name (``NUL``, ``com1``) is refused too: the
    id becomes a directory name, and on Windows ``sessions/NUL`` is the
    null device, not a directory. Session ids are names the program picks,
    so refusing costs nothing (device serials are escaped instead, see
    :func:`device_dir_name`, because ``COM5`` is a real port name there).
    """
    return (
        isinstance(s, str)
        and bool(_SAFE_SESSION_ID_RE.match(s))
        and s.upper() not in _WINDOWS_RESERVED
    )


def is_safe_device(s: object) -> bool:
    """True if `s` is a safe device serial (no traversal). See `workspace_path`.

    Single source of truth for the device-string shape used by:
    `workspace_path(device=)`, `_screenshots_dir`, `_logs_dir`, MCP
    `dhands_log_search`, WS `uart_stream` audit logs, etc. Accepts any
    object — non-str returns False.
    """
    return isinstance(s, str) and bool(_SAFE_DEVICE_RE.match(s))


def is_safe_profile_name(s: object) -> bool:
    """True if `s` is a safe profile name (no traversal). See `profile_path`.

    Accepts any object — non-str returns False. A Windows reserved device
    name is refused: ``profiles/NUL.toml`` is the null device there, with
    or without the extension (same reasoning as :func:`is_safe_session_id`).
    """
    return (
        isinstance(s, str)
        and bool(_SAFE_PROFILE_NAME_RE.match(s))
        and s.upper() not in _WINDOWS_RESERVED
    )


def workspace_root() -> Path:
    """Return the workspace root (where logs, sessions and bench state go).

    First match wins:
        1. DHANDS_WORKSPACE
        2. ``[workspace] root`` in the global config.toml; a relative path
           there is relative to the config file's directory
        3. ``workspace`` under the per-user data directory (infra.dirs)

    The current directory is not looked at. It used to be ("``./workspace``
    if it exists"), which made the answer depend on where a command was
    started — a checkout of another program with its own ``workspace/``
    would have been written into.
    """
    env = os.environ.get("DHANDS_WORKSPACE")
    if env:
        return Path(env).expanduser().resolve()
    configured = _configured_root()
    if configured is not None:
        return configured
    return default_workspace_dir()


def _configured_root() -> Path | None:
    """``[workspace] root`` from the global config.toml, or None.

    Read here rather than through infra.config.load_config(): load_config
    builds a Config whose default workspace comes from this function. A
    missing or broken file means "not configured", same as load_config.
    """
    from devicehands.infra.config import global_config_path

    path = global_config_path()
    try:
        with path.open("rb") as f:
            raw = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    section = raw.get("workspace")
    root = section.get("root") if isinstance(section, dict) else None
    if not isinstance(root, str) or not root.strip():
        return None
    p = Path(root.strip()).expanduser()
    if not p.is_absolute():
        p = path.parent / p
    return p.resolve()


# Base names Windows reserves for devices, with or without an extension
# (``NUL``, ``com5.log``). A serial-port basename such as ``COM5`` is a real
# device name on a Windows bench, so it has to be escaped, not refused.
_WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(10)}
    | {f"LPT{i}" for i in range(10)}
)
# Stands in for ':' in directory names. Never part of a valid serial
# (_SAFE_DEVICE_RE), so the mapping cannot make two serials collide.
_COLON_STAND_IN = "+"


def device_dir_name(device: str) -> str:
    """Directory name for a device serial under ``<workspace>/devices/``.

    A valid serial may contain ``:`` (wireless adb: ``192.0.2.10:5555``),
    which Windows does not allow in a file name. The mapping:

        ':'  -> '+'                       192.0.2.10:5555 -> 192.0.2.10+5555
        a name Windows would still refuse or alter (reserved device name
        such as COM5 / NUL, or a trailing '.') -> wrapped in '+' ... '+'
                                          COM5 -> +COM5+

    '+' never occurs in a valid serial and a valid serial never starts with
    one, so different serials always get different names, and
    :func:`device_from_dir_name` gives the serial back. Serials without
    those characters map to themselves, so existing directories keep their
    names.

    Raises :class:`InvalidDeviceSerial` for anything ``is_safe_device``
    rejects — the result is always exactly one path segment.
    """
    if not is_safe_device(device):
        raise InvalidDeviceSerial(
            f"invalid device {device!r}: must match [A-Za-z0-9._:-]{{1,64}} "
            f"with alnum leading char (rejected to prevent path traversal)"
        )
    name = device.replace(":", _COLON_STAND_IN)
    if name.endswith(".") or name.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
        name = f"{_COLON_STAND_IN}{name}{_COLON_STAND_IN}"
    return name


def device_from_dir_name(name: str) -> str | None:
    """The serial a ``devices/<name>`` directory belongs to, or None when the
    name is not one :func:`device_dir_name` produces (or an old directory
    named after the raw serial, which is accepted as is)."""
    if name.startswith(_COLON_STAND_IN):
        if len(name) < 3 or not name.endswith(_COLON_STAND_IN):
            return None
        name = name[1:-1]
    device = name.replace(_COLON_STAND_IN, ":")
    return device if is_safe_device(device) else None


def device_dir(root: Path, device: str) -> Path:
    """``<root>/devices/<device_dir_name(device)>``."""
    return root / "devices" / device_dir_name(device)


def iso_timestamp() -> str:
    """ISO 8601 timestamp safe for filenames (no colons).

    Example: '2026-04-15T10-30-00'
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")


def workspace_path(
    category: str,
    filename: str,
    *,
    device: str | None = None,
    ensure_dir: bool = True,
) -> Path:
    """Build a canonical artifact path.

    Example:
      workspace_path('logs', 'xxx.txt', device='abc123')
      -> /ws/devices/abc123/logs/xxx.txt

    Raises `InvalidDeviceSerial` when `device` would escape the workspace
    (`..`, absolute paths, separators, non-ASCII, > 64 chars).  Mirrors
    the L-035 root-layer-enforce pattern from `session_path`.  Category
    and filename are internal (caller-controlled) and not validated.
    """
    # Falsy `device` (None / "") takes the no-device path; an unsafe one
    # raises InvalidDeviceSerial here, before anything is resolved.
    dir_name = device_dir_name(device) if device else None
    root = workspace_root()
    if dir_name:
        base = root / "devices" / dir_name / category
    else:
        base = root / category
    if ensure_dir:
        base.mkdir(parents=True, exist_ok=True)
    return base / filename


def resolve_capture_path(
    output: Path | str | None,
    default_name: str,
    *,
    default_category: str = "logs",
    device: str | None = None,
) -> Path:
    """Decide where a capture artifact lands.

    Shared by capture_uart / collect_logcat / collect_dmesg / bugreport so all
    `--output / -o` flags behave the same way (N=4 callers · L-020 abstract).

    Rules:
        - output=None        → workspace_path(default_category, default_name, device=device)
        - output is an existing dir or ends with "/"  → <dir>/<default_name>
          (directory is created if missing)
        - otherwise → treat as exact file path (parent is created)
    """
    if output is None:
        return workspace_path(default_category, default_name, device=device)

    p = Path(output).expanduser()
    looks_like_dir = p.is_dir() or str(output).endswith(("/", "\\"))
    if looks_like_dir:
        p.mkdir(parents=True, exist_ok=True)
        return p / default_name
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def session_path(session_id: str, filename: str = "", *, ensure_dir: bool = True) -> Path:
    """Path inside a session directory.

    Rejects any session_id that would escape `<workspace>/sessions/`:

    - empty / dot / `..` / containing `/` or `\\` / absolute paths
    - any character outside ``[A-Za-z0-9_-]``
    - longer than 128 characters

    These checks are at the root layer (not just the CLI) so every caller
    — `chat_cli` / `session_cli` / future Web UI / future MCP tool — gets
    the same protection without duplicating sanitization. Raises
    :class:`InvalidSessionId` (a `ValueError` subclass) on rejection.
    """
    if not is_safe_session_id(session_id):
        raise InvalidSessionId(
            f"invalid session_id {session_id!r}: must match [A-Za-z0-9][A-Za-z0-9_-]* "
            f"(<= 128 chars); rejected to prevent path traversal"
        )
    root = workspace_root()
    base = root / "sessions" / session_id
    if ensure_dir:
        base.mkdir(parents=True, exist_ok=True)
    # Defence-in-depth: even if regex passes, double-check resolved path is
    # under sessions/. Catches any future regex relaxation that lets a `..`
    # slip through unicode or symlink games.
    sessions_root = (root / "sessions").resolve()
    if not base.resolve().is_relative_to(sessions_root):
        raise InvalidSessionId(
            f"session_id {session_id!r} escapes sessions/ after path resolution"
        )
    return base / filename if filename else base
