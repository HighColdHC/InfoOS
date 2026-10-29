"""Retry policy covers every failure reason."""
from __future__ import annotations

import pytest

from backend.domain.failures.taxonomy import FetchFailureReason
from backend.domain.failures.retry_policy import (
    RetryPolicy,
    RetryStrategy,
    RETRY_POLICIES,
    get_retry_policy,
)


def test_all_reasons_have_policy():
    for reason in FetchFailureReason:
        assert reason in RETRY_POLICIES, f"missing policy for {reason}"


def test_chrome_not_configured_no_retry():
    p = get_retry_policy(FetchFailureReason.CHROME_NOT_CONFIGURED)
    assert p.strategy == RetryStrategy.NO_RETRY
    assert p.max_attempts == 0


def test_opencli_daemon_down_immediate_retry():
    p = get_retry_policy(FetchFailureReason.OPENCLI_DAEMON_DOWN)
    assert p.strategy == RetryStrategy.IMMEDIATE_RETRY
    assert p.max_attempts == 1


def test_opencli_command_failed_exponential_backoff():
    p = get_retry_policy(FetchFailureReason.OPENCLI_COMMAND_FAILED)
    assert p.strategy == RetryStrategy.EXPONENTIAL_BACKOFF
    assert p.delays == (30, 120, 600)  # 30s / 2min / 10min
    assert p.max_attempts == 3


def test_rate_limited_exponential_with_source_threshold():
    p = get_retry_policy(FetchFailureReason.RATE_LIMITED)
    assert p.strategy == RetryStrategy.EXPONENTIAL_BACKOFF
    assert p.delays == (60, 300, 1800)
    assert p.escalate_to_blocked is True


def test_network_timeout_fixed_retry():
    p = get_retry_policy(FetchFailureReason.NETWORK_TIMEOUT)
    assert p.strategy == RetryStrategy.FIXED_RETRY
    assert p.delays == (30, 30, 30)
    assert p.max_attempts == 3


def test_media_download_pending_then_failed():
    p = get_retry_policy(FetchFailureReason.MEDIA_DOWNLOAD_FAILED)
    assert p.strategy == RetryStrategy.FIXED_RETRY
    assert p.delays == (60, 60, 60)
    assert p.max_attempts == 3


def test_get_retry_policy_unknown_raises():
    with pytest.raises(KeyError):
        get_retry_policy("nonexistent_reason")
