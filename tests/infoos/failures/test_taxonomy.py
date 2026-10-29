"""Failure taxonomy contract."""
from __future__ import annotations

import pytest

from backend.domain.failures.taxonomy import FetchFailureReason


def test_all_failure_reasons_present():
    expected = {
        "chrome_not_configured",
        "opencli_daemon_down",
        "browser_session_render_blocked",
        "browser_page_load_failed",
        "platform_not_logged_in",
        "platform_challenge_required",
        "opencli_command_failed",
        "source_unavailable",
        "parse_failed",
        "rate_limited",
        "network_timeout",
        "llm_api_failed",
        "llm_content_rejected",
        "db_write_failed",
        "media_download_failed",
        "video_transcribe_failed",
        "ocr_failed",
        "data_export_failed",
    }
    actual = {r.value for r in FetchFailureReason}
    assert actual == expected


def test_reason_is_str_enum():
    reason = FetchFailureReason.PLATFORM_NOT_LOGGED_IN
    assert isinstance(reason, str)
    assert reason.value == "platform_not_logged_in"


def test_count_includes_source_unavailable_reason():
    assert len(FetchFailureReason) == 18
