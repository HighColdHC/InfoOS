"""Tests for infoos.storage.paths — program/data path resolution."""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.infrastructure.storage.paths import (
    data_dir,
    program_dir,
    resolve_sqlite_path,
    set_data_dir_override,
    reset_data_dir_override,
)


@pytest.fixture
def clean_override() -> None:
    reset_data_dir_override()
    yield
    reset_data_dir_override()


def test_data_dir_defaults_to_xdg_app_support(monkeypatch: pytest.MonkeyPatch, clean_override: None) -> None:
    monkeypatch.setenv("HOME", "/home/user")
    monkeypatch.delenv("INFOOS_DATA_HOME", raising=False)
    expected = Path("/home/user/Library/Application Support/InfoOS")
    assert data_dir() == expected


def test_data_dir_respects_infoos_data_home_env(
    monkeypatch: pytest.MonkeyPatch, clean_override: None,
) -> None:
    monkeypatch.setenv("INFOOS_DATA_HOME", "/custom/data")
    assert data_dir() == Path("/custom/data")


def test_set_data_dir_override_takes_precedence(
    monkeypatch: pytest.MonkeyPatch, clean_override: None,
) -> None:
    monkeypatch.setenv("INFOOS_DATA_HOME", "/env/data")
    set_data_dir_override(Path("/override/data"))
    assert data_dir() == Path("/override/data")
    reset_data_dir_override()
    assert data_dir() == Path("/env/data")


def test_program_dir_defaults_to_user_home(
    monkeypatch: pytest.MonkeyPatch, clean_override: None,
) -> None:
    monkeypatch.setenv("HOME", "/home/user")
    monkeypatch.delenv("INFOOS_HOME", raising=False)
    assert program_dir() == Path("/home/user/InfoOS")


def test_program_dir_respects_infoos_home_env(
    monkeypatch: pytest.MonkeyPatch, clean_override: None,
) -> None:
    monkeypatch.setenv("INFOOS_HOME", "/custom/program")
    assert program_dir() == Path("/custom/program")


def test_resolve_sqlite_path_uses_shared_data_dir_for_current_directory(
    monkeypatch: pytest.MonkeyPatch, clean_override: None,
) -> None:
    monkeypatch.setenv("HOME", "/home/user")
    monkeypatch.delenv("INFOOS_DATA_HOME", raising=False)

    assert resolve_sqlite_path(Path(".")) == Path(
        "/home/user/Library/Application Support/InfoOS/infoos.sqlite3"
    )


def test_resolve_sqlite_path_preserves_explicit_test_base_dir(tmp_path: Path) -> None:
    assert resolve_sqlite_path(tmp_path) == tmp_path / "data" / "infoos.sqlite3"
