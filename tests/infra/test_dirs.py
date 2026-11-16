"""Configuration and data directories (infra.dirs, design 5.10 / 5.11).

Two programs on one computer must not read each other's files. These tests
pin down where DeviceHands looks, that the current directory no longer
decides the workspace, and that per-device directory names are legal on
Windows without two serials ever sharing one.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from devicehands.infra import config as config_mod
from devicehands.infra import dirs
from devicehands.infra import workspace as workspace_mod
from devicehands.infra.workspace import (
    InvalidDeviceSerial,
    device_dir_name,
    device_from_dir_name,
    workspace_path,
    workspace_root,
)

# ─── directories ───────────────────────────────────────────────────


def test_directories_are_named_after_devicehands() -> None:
    for p in (dirs.config_dir(), dirs.data_dir(), dirs.default_workspace_dir(), dirs.tools_dir()):
        assert "devicehands" in p.parts, p
    assert dirs.default_workspace_dir() == dirs.data_dir() / "workspace"
    assert dirs.tools_dir() == dirs.data_dir() / "tools"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="XDG variables apply on Linux")
def test_linux_follows_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    assert dirs.config_dir() == tmp_path / "cfg" / "devicehands"
    assert dirs.data_dir() == tmp_path / "data" / "devicehands"


def test_looking_up_a_directory_creates_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    dirs.config_dir()
    dirs.data_dir()
    dirs.tools_dir()
    assert list(tmp_path.iterdir()) == []


def test_global_config_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DHANDS_CONFIG", str(tmp_path / "x.toml"))
    assert config_mod.global_config_path() == tmp_path / "x.toml"
    monkeypatch.delenv("DHANDS_CONFIG")
    assert config_mod.global_config_path() == dirs.config_dir() / "config.toml"


# ─── workspace_root precedence ─────────────────────────────────────


@pytest.fixture
def real_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, workspace_tripwire) -> Path:
    """workspace_root() with the conftest tripwire taken off: the real config
    lookup, and a default directory under tmp_path."""
    default = tmp_path / "data" / "workspace"
    monkeypatch.setattr(workspace_mod, "_configured_root", workspace_tripwire.real_configured_root)
    monkeypatch.setattr(workspace_mod, "default_workspace_dir", lambda: default)
    monkeypatch.delenv("DHANDS_WORKSPACE")
    monkeypatch.setenv("DHANDS_CONFIG", str(tmp_path / "conf" / "config.toml"))
    return default


def test_default_is_under_the_data_dir_not_the_current_dir(
    real_fallback: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A ./workspace in the current directory used to win. Not any more."""
    cwd = tmp_path / "some-checkout"
    (cwd / "workspace").mkdir(parents=True)
    monkeypatch.chdir(cwd)
    assert workspace_root() == real_fallback


def test_the_real_default_is_dirs_default_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, workspace_tripwire
) -> None:
    monkeypatch.setattr(workspace_mod, "_configured_root", workspace_tripwire.real_configured_root)
    monkeypatch.setattr(workspace_mod, "default_workspace_dir", workspace_tripwire.real_default_dir)
    monkeypatch.delenv("DHANDS_WORKSPACE")
    monkeypatch.setenv("DHANDS_CONFIG", str(tmp_path / "missing.toml"))
    assert workspace_root() == dirs.data_dir() / "workspace"


def test_config_root_is_used(real_fallback: Path, tmp_path: Path) -> None:
    cfg = tmp_path / "conf" / "config.toml"
    cfg.parent.mkdir()
    cfg.write_text(f'[workspace]\nroot = "{(tmp_path / "ws").as_posix()}"\n')
    assert workspace_root() == (tmp_path / "ws").resolve()


def test_relative_config_root_is_relative_to_the_config_file(
    real_fallback: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "conf" / "config.toml"
    cfg.parent.mkdir()
    cfg.write_text('[workspace]\nroot = "ws"\n')
    monkeypatch.chdir(tmp_path)
    assert workspace_root() == (tmp_path / "conf" / "ws").resolve()


def test_env_beats_config(real_fallback: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "conf" / "config.toml"
    cfg.parent.mkdir()
    cfg.write_text('[workspace]\nroot = "/from/config"\n')
    monkeypatch.setenv("DHANDS_WORKSPACE", str(tmp_path / "from-env"))
    assert workspace_root() == (tmp_path / "from-env").resolve()


@pytest.mark.parametrize(
    "text",
    ["", "[workspace]\n", '[workspace]\nroot = ""\n', "[workspace]\nroot = 3\n", "not toml [", 'workspace = "x"\n'],
)
def test_unusable_config_falls_back_to_the_default(real_fallback: Path, tmp_path: Path, text: str) -> None:
    cfg = tmp_path / "conf" / "config.toml"
    cfg.parent.mkdir()
    cfg.write_text(text)
    assert workspace_root() == real_fallback


def test_load_config_reports_the_same_workspace(real_fallback: Path, tmp_path: Path) -> None:
    cfg = tmp_path / "conf" / "config.toml"
    cfg.parent.mkdir()
    cfg.write_text('[workspace]\nroot = "ws"\n')
    assert config_mod.load_config().workspace_root == workspace_root()


# ─── per-device directory names ────────────────────────────────────

# Characters Windows does not allow in a file name.
_WINDOWS_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@pytest.mark.parametrize(
    ("serial", "dirname"),
    [
        ("abc123", "abc123"),  # common case: unchanged
        ("192.0.2.10:5555", "192.0.2.10+5555"),  # wireless adb
        ("emulator-5554", "emulator-5554"),
        ("COM5", "+COM5+"),  # Windows device name
        ("nul", "+nul+"),
        ("com1.log", "+com1.log+"),  # reserved even with an extension
        ("COM10", "COM10"),  # not reserved
        ("abc.", "+abc.+"),  # Windows drops a trailing dot
        ("a:b:c", "a+b+c"),
    ],
)
def test_device_dir_name(serial: str, dirname: str) -> None:
    assert device_dir_name(serial) == dirname
    assert not _WINDOWS_BAD.search(dirname)
    assert device_from_dir_name(dirname) == serial


def test_different_serials_never_share_a_directory() -> None:
    serials = [
        "192.0.2.10:5555", "192.0.2.10_5555", "192.0.2.10-5555", "192.0.2.10.5555",
        "COM5", "COM5:", "COM5.", "nul.:", "nul.", "a.:", "a.", "a:", "a",
    ]
    names = [device_dir_name(s) for s in serials]
    assert len(set(names)) == len(serials), dict(zip(serials, names, strict=True))
    assert [device_from_dir_name(n) for n in names] == serials


@pytest.mark.parametrize("bad", ["..", ".hidden", "a/b", "a\\b", "", "x" * 65, "a+b"])
def test_unsafe_serial_is_refused(bad: str) -> None:
    with pytest.raises(InvalidDeviceSerial):
        device_dir_name(bad)


@pytest.mark.parametrize("name", ["+", "++", "+abc", "a/b", "..", "+a/b+"])
def test_names_that_no_serial_maps_to(name: str) -> None:
    assert device_from_dir_name(name) is None


def test_workspace_path_uses_the_safe_name() -> None:
    p = workspace_path("logs", "x.txt", device="192.0.2.10:5555")
    assert p.parent.parent.name == "192.0.2.10+5555"
    assert ":" not in str(p.relative_to(workspace_root()))
    assert p.parent.is_dir()


def test_flash_job_list_reads_device_names_back(tmp_path: Path) -> None:
    """The flash job list walks devices/ and reports the serial, not the
    directory name; the job id built from it finds the job again."""
    from devicehands.api import flash_route

    job = workspace_path("flash", "flash-boot-2026-01-01T00-00-00", device="192.0.2.10:5555")
    job.mkdir(parents=True)
    roots = dict(flash_route._job_roots())
    assert "192.0.2.10:5555" in roots
    assert roots["192.0.2.10:5555"] == job.parent
    detail = flash_route._job_detail_sync(f"192.0.2.10:5555~{job.name}")
    assert detail is not None


# ─── host tools and doctor ─────────────────────────────────────────


def test_host_tools_go_under_the_data_dir(tmp_path: Path) -> None:
    from devicehands.infra import host_deps

    assert host_deps.default_env().data_dir == dirs.tools_dir()
    # A test environment with a scratch home never reaches the real one.
    env = host_deps.HostEnv(
        home=tmp_path, system="Linux", repo=None, run=lambda *a: (0, ""), fetch=lambda *a: None
    )
    assert env.data_dir == tmp_path / ".local" / "share" / "devicehands" / "tools"
    assert env.bin_dir == tmp_path / ".local" / "bin"  # links stay where they were


def test_doctor_shows_the_directories(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from devicehands.capabilities.doctor import probe_config

    monkeypatch.setenv("DHANDS_CONFIG", str(tmp_path / "c" / "config.toml"))
    by_name = {c.name: c for c in probe_config().checks}
    assert by_name["config dir"].detail == f"{tmp_path / 'c'} (from DHANDS_CONFIG)"
    assert by_name["data dir"].detail == str(dirs.data_dir())
    assert by_name["workspace"].detail == f"{workspace_root()} (from DHANDS_WORKSPACE)"
