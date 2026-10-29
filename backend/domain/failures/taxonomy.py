"""Failure taxonomy for durable task failures.

每种原因对应：
  - task 终态
  - 重试策略（在 retry_policy.py）
  - 用户通知文案（在 notifications.py）

新增原因时必须同步更新：
  - spec §10 失败矩阵
  - retry_policy.RETRY_POLICIES 字典
  - notifications.NOTIFICATION_COPY 字典
"""
from __future__ import annotations

from enum import Enum


class FetchFailureReason(str, Enum):
    """Durable fetch and processing failure reasons."""

    CHROME_NOT_CONFIGURED = "chrome_not_configured"
    OPENCLI_DAEMON_DOWN = "opencli_daemon_down"
    BROWSER_PAGE_LOAD_FAILED = "browser_page_load_failed"
    BROWSER_SESSION_RENDER_BLOCKED = "browser_session_render_blocked"
    PLATFORM_CHALLENGE_REQUIRED = "platform_challenge_required"
    PLATFORM_NOT_LOGGED_IN = "platform_not_logged_in"
    SOURCE_UNAVAILABLE = "source_unavailable"
    OPENCLI_COMMAND_FAILED = "opencli_command_failed"
    PARSE_FAILED = "parse_failed"
    RATE_LIMITED = "rate_limited"
    NETWORK_TIMEOUT = "network_timeout"
    LLM_API_FAILED = "llm_api_failed"
    LLM_CONTENT_REJECTED = "llm_content_rejected"
    DB_WRITE_FAILED = "db_write_failed"
    MEDIA_DOWNLOAD_FAILED = "media_download_failed"
    VIDEO_TRANSCRIBE_FAILED = "video_transcribe_failed"
    OCR_FAILED = "ocr_failed"
    DATA_EXPORT_FAILED = "data_export_failed"
