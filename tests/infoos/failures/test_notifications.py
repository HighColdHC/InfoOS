"""Notification copy covers every failure reason."""
from __future__ import annotations

from backend.domain.failures.taxonomy import FetchFailureReason
from backend.domain.failures.notifications import (
    NotificationLevel,
    NOTIFICATION_COPY,
    get_notification,
)


def test_all_reasons_have_copy():
    for reason in FetchFailureReason:
        assert reason in NOTIFICATION_COPY


def test_levels_in_use():
    used = {n.level for n in NOTIFICATION_COPY.values()}
    # spec §10.2 至少包含 silent / desktop / desktop_and_web
    assert NotificationLevel.SILENT in used
    assert NotificationLevel.DESKTOP in used


def test_chrome_not_configured_has_desktop_and_web():
    n = get_notification(FetchFailureReason.CHROME_NOT_CONFIGURED)
    assert n.level == NotificationLevel.DESKTOP_AND_WEB
    assert "setup" in n.copy.lower() or "browser" in n.copy.lower()


def test_platform_not_logged_in_copy_mentions_platform():
    n = get_notification(FetchFailureReason.PLATFORM_NOT_LOGGED_IN)
    assert "登录" in n.copy or "login" in n.copy.lower()


def test_parse_failed_copy_mentions_dom():
    n = get_notification(FetchFailureReason.PARSE_FAILED)
    assert "解析" in n.copy or "DOM" in n.copy or "selector" in n.copy.lower()
