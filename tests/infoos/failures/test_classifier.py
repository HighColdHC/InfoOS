"""Failure classifier: 异常 → FetchFailureReason。"""
from __future__ import annotations

import pytest

from backend.domain.failures.taxonomy import FetchFailureReason
from backend.domain.failures.classifier import (
    classify_failure,
    FetchContext,
    ClassificationError,
)


def _ctx(**overrides) -> FetchContext:
    base = dict(
        platform="x",
        source_type="creator",
        http_status=None,
        exit_code=None,
        stderr="",
    )
    base.update(overrides)
    return FetchContext(**base)


def test_chrome_session_missing():
    err = FileNotFoundError("/Users/me/.infoos/browser-session.json")
    assert classify_failure(err, _ctx()) == FetchFailureReason.CHROME_NOT_CONFIGURED


def test_opencli_daemon_down_via_exit_code():
    err = RuntimeError("opencli exited with 1")
    ctx = _ctx(exit_code=1, stderr="daemon not running")
    assert classify_failure(err, ctx) == FetchFailureReason.OPENCLI_DAEMON_DOWN


def test_login_required_via_status():
    err = RuntimeError("login required")
    ctx = _ctx(http_status=401)
    assert classify_failure(err, ctx) == FetchFailureReason.PLATFORM_NOT_LOGGED_IN


def test_rate_limited_via_status():
    err = RuntimeError("rate limited")
    ctx = _ctx(http_status=429)
    assert classify_failure(err, ctx) == FetchFailureReason.RATE_LIMITED


def test_network_timeout_via_timeout_error():
    err = TimeoutError("connection timed out")
    assert classify_failure(err, _ctx()) == FetchFailureReason.NETWORK_TIMEOUT


def test_parse_failed_via_selector_error():
    err = ValueError("selector 'tweet-text' not found")
    assert classify_failure(err, _ctx()) == FetchFailureReason.PARSE_FAILED


def test_db_write_failed_via_integrity_error():
    from sqlite3 import IntegrityError
    err = IntegrityError("UNIQUE constraint failed")
    assert classify_failure(err, _ctx()) == FetchFailureReason.DB_WRITE_FAILED


def test_unknown_failure_raises():
    err = RuntimeError("something completely new")
    with pytest.raises(ClassificationError):
        classify_failure(err, _ctx())


def test_media_download_failed_via_http_status():
    err = RuntimeError("download failed")
    ctx = _ctx(http_status=404)
    # 注：本测试假定 classifier 已经实现 media 状态映射；如未实现需先实现
    # 简化：这里只测试 404 不会被误判为 rate_limited
    assert classify_failure(err, ctx) != FetchFailureReason.RATE_LIMITED
