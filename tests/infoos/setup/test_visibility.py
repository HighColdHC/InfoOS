"""Visibility config: read/write browser.headless_during_fetch in config.yaml.

Per spec section 2.4 / 4.1: a single bool that controls the runtime
window mode. Setup / login always run GUI regardless of this value.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.application.setup import visibility  # noqa: E402


def test_read_returns_true_when_key_missing(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("sources: []\n", encoding="utf-8")
    assert visibility.read_headless_during_fetch(config) is True


def test_read_returns_stored_true(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("browser:\n  headless_during_fetch: true\n", encoding="utf-8")
    assert visibility.read_headless_during_fetch(config) is True


def test_read_returns_true_for_missing_file(tmp_path):
    assert visibility.read_headless_during_fetch(tmp_path / "missing") is True


def test_read_returns_stored_false(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("browser:\n  headless_during_fetch: false\n", encoding="utf-8")
    assert visibility.read_headless_during_fetch(config) is False


def test_write_round_trip(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("sources: []\n", encoding="utf-8")
    visibility.write_headless_during_fetch(True, config)
    assert visibility.read_headless_during_fetch(config) is True
    visibility.write_headless_during_fetch(False, config)
    assert visibility.read_headless_during_fetch(config) is False


def test_write_preserves_unknown_top_level_keys(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "feeds:\n  - url: https://example.com/feed\nuser_notes: hello\n",
        encoding="utf-8",
    )
    visibility.write_headless_during_fetch(False, config)
    text = config.read_text(encoding="utf-8")
    assert "feeds:" in text
    assert "user_notes: hello" in text
    assert "headless_during_fetch: false" in text


def test_write_is_noop_when_value_unchanged(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("browser:\n  headless_during_fetch: true\n", encoding="utf-8")
    original = config.read_text(encoding="utf-8")
    visibility.write_headless_during_fetch(True, config)
    assert config.read_text(encoding="utf-8") == original
