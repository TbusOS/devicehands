"""Load `.env.local` / `.env` files at CLI/API startup.

dhands's deployment-specific settings (Ollama URL, default model, API port,
SSH host, …) are all `DHANDS_*` environment variables read via typer's
`envvar=` parameter or direct `os.environ.get`.

In addition to shell `export` and Claude Code / Cursor MCP config, the
values can sit in a `.env.local` file (template: `.env.example` in the
repository):

    .env.local     # your real values; gitignored in a checkout
    .env           # shared defaults (optional)

Where the files are looked for, first folder that has the file wins:
    1. this project's source checkout, when the code runs from one
       (infra.resources.checkout_root: pyproject.toml names devicehands)
    2. the per-user config dir (infra.dirs.config_dir,
       Linux: ~/.config/devicehands)

The current directory is not looked at. It used to be, and running dhands
inside some other project then pulled that project's whole `.env` into
os.environ — and on into every subprocess dhands starts.

Priority (highest first):
    1. Existing shell environment (`export FOO=bar` in ~/.bashrc etc.)
    2. `.env.local`
    3. `.env`
    4. Library / CLI flag defaults

This module never overwrites an environment variable that is already
present — the shell always wins. This keeps `export X=...` / `X=... dhands …`
predictable.
"""

from __future__ import annotations

import os
from pathlib import Path

from devicehands.infra.dirs import config_dir
from devicehands.infra.resources import checkout_root


def load_env_files(roots: list[Path] | None = None) -> list[Path]:
    """Load `.env.local` then `.env` from the given roots into `os.environ`.

    Args:
        roots: candidate directories. Default: default_roots() — this
            project's checkout (if the code runs from one), then the
            per-user config dir.

    Returns:
        List of files that were actually loaded (for logging/debugging).

    Never overwrites variables already present in `os.environ`.
    """
    if roots is None:
        roots = default_roots()

    loaded: list[Path] = []
    # .env.local has higher priority than .env; load .local first so .env
    # won't overwrite anything .local set (both use "don't override shell"
    # policy, but order still matters for vars unique to .env).
    for name in (".env.local", ".env"):
        for root in roots:
            candidate = root / name
            if candidate.is_file():
                _load_one(candidate)
                loaded.append(candidate)
                break  # one file per name — first root wins
    return loaded


def default_roots() -> list[Path]:
    """Checkout root (only when running from this project's checkout), then
    the per-user config dir. Dedup while preserving order."""
    seen: set[Path] = set()
    out: list[Path] = []
    for r in (checkout_root(), config_dir()):
        if r is None:
            continue
        r = r.resolve()
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def _load_one(path: Path) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        # Strip optional `export ` prefix (dotenv convention)
        if s.startswith("export "):
            s = s[len("export ") :].lstrip()
        if "=" not in s:
            continue
        k, _, v = s.partition("=")
        k = k.strip()
        v = v.strip()
        # Strip surrounding quotes
        if len(v) >= 2 and (
            (v[0] == v[-1] == '"') or (v[0] == v[-1] == "'")
        ):
            v = v[1:-1]
        if not k:
            continue
        if k in os.environ:
            continue  # shell wins
        os.environ[k] = v
