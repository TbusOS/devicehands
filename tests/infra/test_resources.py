"""Tests for devicehands.infra.resources (shipped web UI / bench files)."""

from __future__ import annotations

import importlib.resources
import tomllib
from pathlib import Path

import pytest

from devicehands.infra import resources

REPO_ROOT = Path(__file__).resolve().parents[2]

# Tests run against a non-editable install (`pip install .`) import the
# package from site-packages, where checkout fallbacks do not apply.
_from_checkout = pytest.mark.skipif(
    resources.checkout_root() is None,
    reason="devicehands imported from an installed copy, not this checkout",
)


def _fake_tree(root: Path, project_name: str | None) -> Path:
    """<root>/src/devicehands/infra/resources.py, with a pyproject naming
    `project_name` (None = no pyproject at all). Returns the module path."""
    mod = root / "src" / "devicehands" / "infra" / "resources.py"
    mod.parent.mkdir(parents=True)
    mod.write_text("", encoding="utf-8")
    if project_name is not None:
        (root / "pyproject.toml").write_text(
            f'[project]\nname = "{project_name}"\n', encoding="utf-8"
        )
    return mod


# ─── is_checkout / checkout_root ──────────────────────────────────


def test_is_checkout_true_for_this_project(tmp_path: Path) -> None:
    _fake_tree(tmp_path, "devicehands")
    assert resources.is_checkout(tmp_path)


@pytest.mark.parametrize("content", [
    '[project]\nname = "someone-else"\n',
    '[tool.other]\nx = 1\n',
    'project = "devicehands"\n',
    "this is [ not toml",
])
def test_is_checkout_false_for_other_or_broken_pyproject(tmp_path: Path, content: str) -> None:
    (tmp_path / "pyproject.toml").write_text(content, encoding="utf-8")
    assert not resources.is_checkout(tmp_path)


def test_is_checkout_false_without_pyproject(tmp_path: Path) -> None:
    assert not resources.is_checkout(tmp_path)


@_from_checkout
def test_checkout_root_of_this_repo() -> None:
    # The tests run from the source checkout.
    assert resources.checkout_root() == REPO_ROOT


def test_checkout_root_ignores_another_projects_pyproject(tmp_path: Path) -> None:
    """An installed copy whose parent folders happen to hold some other
    project's pyproject.toml is not a checkout. (The old docs/app lookup
    took the first pyproject.toml it met, whoever it belonged to.)"""
    mod = _fake_tree(tmp_path, "someone-else")
    (tmp_path / "docs" / "app").mkdir(parents=True)
    assert resources.checkout_root(mod) is None


def test_checkout_root_none_without_pyproject(tmp_path: Path) -> None:
    mod = _fake_tree(tmp_path, None)
    assert resources.checkout_root(mod) is None


def test_checkout_root_found_in_fake_checkout(tmp_path: Path) -> None:
    mod = _fake_tree(tmp_path, "devicehands")
    assert resources.checkout_root(mod) == tmp_path.resolve()


# ─── resource_dir ─────────────────────────────────────────────────


@_from_checkout
def test_resource_dir_falls_back_to_checkout() -> None:
    # A source checkout has no src/devicehands/_resources/.
    assert not (REPO_ROOT / "src" / "devicehands" / resources.RESOURCE_SUBDIR).exists()
    assert resources.resource_dir(resources.WEB) == REPO_ROOT / "docs" / "app"
    assert resources.resource_dir(resources.BENCH) == REPO_ROOT / "bench-py"


def _fake_installed_package(monkeypatch: pytest.MonkeyPatch, pkg: Path) -> None:
    real_files = importlib.resources.files

    def files(anchor):  # type: ignore[no-untyped-def]
        if anchor == resources.PROJECT_NAME:
            return pkg
        return real_files(anchor)

    monkeypatch.setattr(importlib.resources, "files", files)


def test_installed_copy_wins_over_checkout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pkg = tmp_path / "site-packages" / "devicehands"
    web = pkg / "_resources" / "web"
    bench = pkg / "_resources" / "bench"
    web.mkdir(parents=True)
    bench.mkdir(parents=True)
    _fake_installed_package(monkeypatch, pkg)
    assert resources.resource_dir(resources.WEB) == web
    assert resources.resource_dir(resources.BENCH) == bench


def test_installed_without_checkout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pkg = tmp_path / "site-packages" / "devicehands"
    (pkg / "_resources" / "web").mkdir(parents=True)
    _fake_installed_package(monkeypatch, pkg)
    monkeypatch.setattr(resources, "checkout_root", lambda module_file=None: None)
    assert resources.resource_dir(resources.WEB) == pkg / "_resources" / "web"
    # bench was not shipped in this fake package and there is no checkout.
    assert resources.resource_dir(resources.BENCH) is None
    assert resources.expected_dir(resources.BENCH) == (
        Path(resources.__file__).resolve().parents[1] / "_resources" / "bench"
    )


def test_nothing_anywhere_is_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fake_installed_package(monkeypatch, tmp_path / "empty-pkg")
    monkeypatch.setattr(resources, "checkout_root", lambda module_file=None: None)
    assert resources.resource_dir(resources.WEB) is None


def test_unknown_resource_name_rejected() -> None:
    with pytest.raises(ValueError):
        resources.resource_dir("nope")
    with pytest.raises(ValueError):
        resources.expected_dir("nope")


# ─── pyproject.toml packs what the lookup expects ─────────────────


def _hatch() -> dict:
    with (REPO_ROOT / "pyproject.toml").open("rb") as f:
        return tomllib.load(f)["tool"]["hatch"]["build"]


def test_wheel_maps_checkout_dirs_to_resources() -> None:
    """The wheel side of CHECKOUT_DIRS lives in pyproject.toml; the two must
    agree or an installed hub finds no web UI / no bench files."""
    wheel = _hatch()["targets"]["wheel"]
    sources = wheel["sources"]
    for name, parts in resources.CHECKOUT_DIRS.items():
        src = "/".join(parts)
        assert src in wheel["only-include"], src
        assert sources.get(src) == f"devicehands/{resources.RESOURCE_SUBDIR}/{name}", src
        assert (REPO_ROOT / src).is_dir(), src
    assert "src/devicehands" in wheel["only-include"]
    assert sources.get("src") == ""


def test_local_config_files_never_packed() -> None:
    """bench-py/bench.conf and friends hold real tokens and hosts. hatch's
    own .gitignore handling missed `**/bench.conf` when this was written, so
    the build config names them itself (for wheel and sdist alike)."""
    exclude = _hatch()["exclude"]
    for name in ("bench.conf", "agent.conf", ".dhands-bench", "hub.env", ".env.local"):
        assert name in exclude, name
