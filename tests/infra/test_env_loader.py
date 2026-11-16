"""Tests for devicehands.infra.env_loader."""

from __future__ import annotations

import os

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
