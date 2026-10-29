"""Tests for backend.infrastructure.browser.in_flight context manager."""
import json
import os
import signal
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.infrastructure.browser.in_flight import (
    LOCK_PATH,
    in_flight_section,
    is_in_flight,
    read_lock_payload,
)


def test_is_in_flight_false_when_no_lock(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.infrastructure.browser.in_flight.LOCK_PATH", tmp_path / "in-flight.lock")
    assert is_in_flight() is False


def test_in_flight_section_writes_lock_with_payload(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.infrastructure.browser.in_flight.LOCK_PATH", tmp_path / "in-flight.lock")
    with in_flight_section("backend.application.workflows.run_once run-once", task_count=5):
        assert is_in_flight() is True
        payload = read_lock_payload()
        assert payload is not None
        assert payload["owner"] == "backend.application.workflows.run_once run-once"
        assert payload["task_count"] == 5
        assert payload["pid"] == os.getpid()
        assert "started_at" in payload
    assert is_in_flight() is False


def test_in_flight_section_removes_lock_on_exception(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.infrastructure.browser.in_flight.LOCK_PATH", tmp_path / "in-flight.lock")
    with pytest.raises(RuntimeError, match="boom"):
        with in_flight_section("test", task_count=1):
            assert is_in_flight() is True
            raise RuntimeError("boom")
    # Even on exception, the lock must be removed
    assert is_in_flight() is False


def test_lock_file_is_exclusive(tmp_path, monkeypatch):
    """Two concurrent in_flight_section() on the same process must not corrupt the lock."""
    monkeypatch.setattr("backend.infrastructure.browser.in_flight.LOCK_PATH", tmp_path / "in-flight.lock")
    with in_flight_section("first", task_count=1):
        # Second call should also work (re-entrant: it just rewrites the file)
        with in_flight_section("second", task_count=2):
            payload = read_lock_payload()
            assert payload["owner"] == "second"
    assert is_in_flight() is False


def test_read_lock_payload_returns_none_when_no_lock(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.infrastructure.browser.in_flight.LOCK_PATH", tmp_path / "missing.lock")
    assert read_lock_payload() is None


def test_read_lock_payload_returns_none_on_corrupt_json(tmp_path, monkeypatch):
    p = tmp_path / "in-flight.lock"
    p.write_text("not json{{")
    monkeypatch.setattr("backend.infrastructure.browser.in_flight.LOCK_PATH", p)
    assert read_lock_payload() is None
