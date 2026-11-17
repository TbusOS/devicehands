"""Find the non-Python files DeviceHands ships: the web UI and the bench agent.

Two ways the code runs, two places the files are:

    installed wheel                    source checkout (also `uv sync`,
                                       which installs it editable)
    site-packages/devicehands/         <checkout>/src/devicehands/
      _resources/web/    <-- web       <checkout>/docs/app/
      _resources/bench/  <-- bench     <checkout>/bench-py/

pyproject.toml copies docs/app/ and bench-py/ into ``_resources/`` when
the wheel is built (``[tool.hatch.build.targets.wheel.sources]``). A
checkout has no ``_resources/``, so the lookup falls back to the checkout's
own folders.

The fallback only trusts a folder that is this project's checkout: the
pyproject.toml three levels above this file must name the project
``devicehands``. Without that check an installed copy would pick up
whatever pyproject.toml happened to sit above site-packages.

Callers get a real directory or None; nothing here creates files.
"""

from __future__ import annotations

import importlib.resources
import tomllib
from pathlib import Path

PROJECT_NAME = "devicehands"
WEB = "web"
BENCH = "bench"

# Resource name -> its folder in a source checkout, relative to the root.
# The wheel side of the mapping lives in pyproject.toml; a test keeps the
# two in step.
CHECKOUT_DIRS: dict[str, tuple[str, ...]] = {
    WEB: ("docs", "app"),
    BENCH: ("bench-py",),
}
RESOURCE_SUBDIR = "_resources"


def is_checkout(root: Path) -> bool:
    """True when `root` holds this project's pyproject.toml."""
    try:
        with (root / "pyproject.toml").open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return False
    project = data.get("project")
    return isinstance(project, dict) and project.get("name") == PROJECT_NAME


def checkout_root(module_file: Path | None = None) -> Path | None:
    """The source checkout this code runs from, or None for an install.

    `module_file` stands in for this file's path (tests).
    """
    here = (module_file or Path(__file__)).resolve()
    if len(here.parents) < 4:
        return None
    # <root>/src/devicehands/infra/resources.py
    root = here.parents[3]
    return root if is_checkout(root) else None


def _installed_dir(name: str) -> Path | None:
    """`_resources/<name>` inside the installed package, if present.

    A wheel installed by pip is always unpacked to plain files, so the
    package root is a Path. Anything else (a zip import) is not supported.
    """
    base = importlib.resources.files(PROJECT_NAME).joinpath(RESOURCE_SUBDIR, name)
    if isinstance(base, Path) and base.is_dir():
        return base
    return None


def _checkout_dir(name: str, root: Path | None) -> Path | None:
    if root is None:
        return None
    candidate = root.joinpath(*CHECKOUT_DIRS[name])
    return candidate if candidate.is_dir() else None


def resource_dir(name: str) -> Path | None:
    """Folder of shipped resource `name` (WEB or BENCH), or None.

    The installed copy wins; a source checkout is the fallback.
    """
    if name not in CHECKOUT_DIRS:
        raise ValueError(f"unknown resource {name!r}")
    return _installed_dir(name) or _checkout_dir(name, checkout_root())


def expected_dir(name: str) -> Path:
    """Where resource `name` should be, for an error message when
    resource_dir() found nothing: the checkout folder in a checkout, the
    package's _resources folder otherwise."""
    if name not in CHECKOUT_DIRS:
        raise ValueError(f"unknown resource {name!r}")
    root = checkout_root()
    if root is not None:
        return root.joinpath(*CHECKOUT_DIRS[name])
    return Path(__file__).resolve().parents[1] / RESOURCE_SUBDIR / name
