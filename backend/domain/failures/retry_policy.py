"""Retry policy for durable failure reasons.

策略语义：
  - NO_RETRY: 不自动重试（典型：用户配置类错误）
  - IMMEDIATE_RETRY: 立即重试 1 次（典型：守护类失败）
  - FIXED_RETRY: 固定间隔重试 N 次
  - EXPONENTIAL_BACKOFF: 指数退避重试 N 次
  - ESCALATE_TO_BLOCKED: 重试耗尽后转 blocked（典型：rate limit 超阈值）
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from backend.domain.failures.taxonomy import FetchFailureReason


class RetryStrategy(str, Enum):
    NO_RETRY = "no_retry"
    IMMEDIATE_RETRY = "immediate_retry"
    FIXED_RETRY = "fixed_retry"
    EXPONENTIAL_BACKOFF = "exponential_backoff"


@dataclass(frozen=True)
class RetryPolicy:
    strategy: RetryStrategy
    max_attempts: int  # Additional retries after the initial execution.
    delays: tuple[int, ...]  # 秒
    escalate_to_blocked: bool = False


# Durable retry policy table.
RETRY_POLICIES: dict[FetchFailureReason, RetryPolicy] = {
    FetchFailureReason.CHROME_NOT_CONFIGURED: RetryPolicy(
        RetryStrategy.NO_RETRY, 0, ()
    ),
    FetchFailureReason.OPENCLI_DAEMON_DOWN: RetryPolicy(
        RetryStrategy.IMMEDIATE_RETRY, 1, (0,)
    ),
    FetchFailureReason.BROWSER_PAGE_LOAD_FAILED: RetryPolicy(
        RetryStrategy.FIXED_RETRY, 3, (30, 30, 30)
    ),
    FetchFailureReason.BROWSER_SESSION_RENDER_BLOCKED: RetryPolicy(
        RetryStrategy.EXPONENTIAL_BACKOFF, 2, (30, 120),
        escalate_to_blocked=True,
    ),
    FetchFailureReason.PLATFORM_CHALLENGE_REQUIRED: RetryPolicy(
        RetryStrategy.NO_RETRY, 0, ()
    ),
    FetchFailureReason.PLATFORM_NOT_LOGGED_IN: RetryPolicy(
        RetryStrategy.NO_RETRY, 0, ()  # 由 login_probe 触发，不在这里重试
    ),
    FetchFailureReason.SOURCE_UNAVAILABLE: RetryPolicy(
        RetryStrategy.NO_RETRY, 0, ()
    ),
    FetchFailureReason.OPENCLI_COMMAND_FAILED: RetryPolicy(
        RetryStrategy.EXPONENTIAL_BACKOFF, 3, (30, 120, 600)
    ),
    FetchFailureReason.PARSE_FAILED: RetryPolicy(
        RetryStrategy.NO_RETRY, 0, ()
    ),
    FetchFailureReason.RATE_LIMITED: RetryPolicy(
        RetryStrategy.EXPONENTIAL_BACKOFF, 3, (60, 300, 1800),
        escalate_to_blocked=True
    ),
    FetchFailureReason.NETWORK_TIMEOUT: RetryPolicy(
        RetryStrategy.FIXED_RETRY, 3, (30, 30, 30)
    ),
    FetchFailureReason.LLM_API_FAILED: RetryPolicy(
        RetryStrategy.EXPONENTIAL_BACKOFF, 3, (30, 120, 600)
    ),
    FetchFailureReason.LLM_CONTENT_REJECTED: RetryPolicy(
        RetryStrategy.NO_RETRY, 0, ()  # 直接标 cleaned
    ),
    FetchFailureReason.DB_WRITE_FAILED: RetryPolicy(
        RetryStrategy.FIXED_RETRY, 3, (5, 5, 5)
    ),
    FetchFailureReason.MEDIA_DOWNLOAD_FAILED: RetryPolicy(
        RetryStrategy.FIXED_RETRY, 3, (60, 60, 60)
    ),
    FetchFailureReason.VIDEO_TRANSCRIBE_FAILED: RetryPolicy(
        RetryStrategy.EXPONENTIAL_BACKOFF, 2, (60, 300)
    ),
    FetchFailureReason.OCR_FAILED: RetryPolicy(
        RetryStrategy.FIXED_RETRY, 2, (30, 30)
    ),
    FetchFailureReason.DATA_EXPORT_FAILED: RetryPolicy(
        RetryStrategy.FIXED_RETRY, 2, (5, 30)
    ),
}


def get_retry_policy(reason: FetchFailureReason) -> RetryPolicy:
    return RETRY_POLICIES[reason]
