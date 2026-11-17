"""Tests for devicehands.infra.env_loader."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from devicehands.infra import env_loader
from devicehands.infra.env_loader import load_env_files


def test_loads_env_local_into_environ(tmp_path, monkeypatch):
    (tmp_path / ".env.local").write_text(
        "DHANDS_TEST_KEY1=value1\nDHANDS_TEST_KEY2=value2\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("DHANDS_TEST_KEY1", raising=False)
    monkeypatch.delenv("DHANDS_TEST_KEY2", raising=False)

    loaded = load_env_files(roots=[tmp_path])

    assert len(loaded) == 1
    assert loaded[0].name == ".env.local"
    assert os.environ["DHANDS_TEST_KEY1"] == "value1"
    assert os.environ["DHANDS_TEST_KEY2"] == "value2"


def test_shell_env_wins_over_file(tmp_path, monkeypatch):
    (tmp_path / ".env.local").write_text("DHANDS_PRECEDENCE=from_file\n", encoding="utf-8")
    monkeypatch.setenv("DHANDS_PRECEDENCE", "from_shell")

    load_env_files(roots=[tmp_path])

    assert os.environ["DHANDS_PRECEDENCE"] == "from_shell"


def test_env_local_takes_priority_over_env(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("DHANDS_TWO_FILE_KEY=from_env\n", encoding="utf-8")
    (tmp_path / ".env.local").write_text("DHANDS_TWO_FILE_KEY=from_local\n", encoding="utf-8")
    monkeypatch.delenv("DHANDS_TWO_FILE_KEY", raising=False)

    load_env_files(roots=[tmp_path])

    assert os.environ["DHANDS_TWO_FILE_KEY"] == "from_local"


def test_comments_and_blanks_ignored(tmp_path, monkeypatch):
    (tmp_path / ".env.local").write_text(
        "# full comment\n"
        "\n"
        "   \n"
        "DHANDS_REAL=real\n"
        "  # indented comment\n"
        "not a key because no equals sign\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("DHANDS_REAL", raising=False)

    load_env_files(roots=[tmp_path])

    assert os.environ["DHANDS_REAL"] == "real"


def test_quotes_stripped(tmp_path, monkeypatch):
    (tmp_path / ".env.local").write_text(
        'DHANDS_DQ="value with spaces"\n'
        "DHANDS_SQ='single'\n"
        "DHANDS_NQ=plain\n",
        encoding="utf-8",
    )
    for k in ("DHANDS_DQ", "DHANDS_SQ", "DHANDS_NQ"):
        monkeypatch.delenv(k, raising=False)

    load_env_files(roots=[tmp_path])

    assert os.environ["DHANDS_DQ"] == "value with spaces"
    assert os.environ["DHANDS_SQ"] == "single"
    assert os.environ["DHANDS_NQ"] == "plain"


def test_export_prefix_allowed(tmp_path, monkeypatch):
    (tmp_path / ".env.local").write_text(
        "export DHANDS_WITH_EXPORT=ok\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("DHANDS_WITH_EXPORT", raising=False)

    load_env_files(roots=[tmp_path])

    assert os.environ["DHANDS_WITH_EXPORT"] == "ok"


def test_no_files_returns_empty(tmp_path):
    loaded = load_env_files(roots=[tmp_path])
    assert loaded == []


# ─── Default search roots: checkout, then config dir; never the cwd ──────


@pytest.fixture
def places(tmp_path, monkeypatch):
    """Separate folders for cwd / checkout / config dir, wired into env_loader."""
    cwd = tmp_path / "other-project"
    checkout = tmp_path / "checkout"
    cfg = tmp_path / "config"
    for d in (cwd, checkout, cfg):
        d.mkdir()
    monkeypatch.chdir(cwd)
    # raising=False: the reverse check runs these tests against the old module,
    # which has neither name.
    monkeypatch.setattr(env_loader, "checkout_root", lambda: checkout, raising=False)
    monkeypatch.setattr(env_loader, "config_dir", lambda: cfg, raising=False)
    # setenv first so teardown removes whatever load_env_files() put there.
    for k in ("DHANDS_T_CWD", "DHANDS_T_WHERE", "DHANDS_T_CFG_ONLY", "OTHER_PROJECT_SECRET"):
        monkeypatch.setenv(k, "")
        monkeypatch.delenv(k)
    return {"cwd": cwd, "checkout": checkout, "cfg": cfg}


def test_current_directory_is_not_read(places):
    """Running dhands inside another project must not pull in its .env."""
    (places["cwd"] / ".env.local").write_text(
        "DHANDS_T_CWD=1\nOTHER_PROJECT_SECRET=x\n", encoding="utf-8"
    )
    (places["cwd"] / ".env").write_text("DHANDS_T_CWD=2\n", encoding="utf-8")

    assert load_env_files() == []
    assert "DHANDS_T_CWD" not in os.environ
    assert "OTHER_PROJECT_SECRET" not in os.environ
    assert Path.cwd().resolve() not in env_loader.default_roots()


def test_config_dir_is_read(places):
    (places["cfg"] / ".env.local").write_text("DHANDS_T_WHERE=config\n", encoding="utf-8")

    loaded = load_env_files()

    assert loaded == [places["cfg"].resolve() / ".env.local"]
    assert os.environ["DHANDS_T_WHERE"] == "config"


def test_checkout_wins_over_config_dir(places):
    (places["checkout"] / ".env.local").write_text("DHANDS_T_WHERE=checkout\n", encoding="utf-8")
    (places["cfg"] / ".env.local").write_text(
        "DHANDS_T_WHERE=config\nDHANDS_T_CFG_ONLY=1\n", encoding="utf-8"
    )

    loaded = load_env_files()

    # One file per name: the checkout's .env.local, the config dir's is skipped.
    assert loaded == [places["checkout"].resolve() / ".env.local"]
    assert os.environ["DHANDS_T_WHERE"] == "checkout"
    assert "DHANDS_T_CFG_ONLY" not in os.environ


def test_installed_copy_reads_config_dir_only(places, monkeypatch):
    """An installed copy has no checkout: only the config dir is searched."""
    monkeypatch.setattr(env_loader, "checkout_root", lambda: None)
    (places["checkout"] / ".env.local").write_text("DHANDS_T_WHERE=checkout\n", encoding="utf-8")
    (places["cfg"] / ".env").write_text("DHANDS_T_WHERE=config-env\n", encoding="utf-8")

    assert env_loader.default_roots() == [places["cfg"].resolve()]
    load_env_files()
    assert os.environ["DHANDS_T_WHERE"] == "config-env"


def test_default_roots_dedup(places, monkeypatch):
    monkeypatch.setattr(env_loader, "config_dir", lambda: places["checkout"])
    assert env_loader.default_roots() == [places["checkout"].resolve()]
