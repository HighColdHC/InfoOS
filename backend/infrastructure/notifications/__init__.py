from __future__ import annotations

from dataclasses import asdict, dataclass
from collections.abc import Callable
from typing import Any
from pathlib import Path
from uuid import uuid4

from backend.infrastructure.storage.base import utc_now
from backend.infrastructure.observability.logging import get_logger


logger = get_logger("notifications")


@dataclass(frozen=True)
class NotificationRecord:
    notification_id: str
    created_at: str
    kind: str
    severity: str
    title: str
    body: str
    source_id: str | None = None
    topic_id: str | None = None
    run_id: str | None = None
    details: dict[str, Any] | None = None

    @classmethod
    def create(
        cls,
        *,
        kind: str,
        severity: str,
        title: str,
        body: str,
        source_id: str | None = None,
        topic_id: str | None = None,
        run_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> "NotificationRecord":
        return cls(
            notification_id=f"notif_{uuid4().hex[:12]}",
            created_at=utc_now(),
            kind=kind,
            severity=severity,
            title=title,
            body=body,
            source_id=source_id,
            topic_id=topic_id,
            run_id=run_id,
            details=details or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


from backend.infrastructure.notifications.email_sender import send_email
from backend.infrastructure.notifications.rules import get_config, is_due, record_sent, is_daily_brief_time
from backend.infrastructure.notifications.templates import (
    format_daily_brief_email,
    format_high_priority_email,
    format_source_failure_email,
)


def _latest_source_records(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return only the latest attempt per source_id."""
    latest: dict[str, dict[str, Any]] = {}
    for source in sources:
        sid = source.get("source_id")
        if not sid:
            continue
        current = latest.get(sid)
        if current is None or str(source.get("finished_at") or "") > str(current.get("finished_at") or ""):
            latest[sid] = source
    return list(latest.values())


def _is_browser_session_failure(source: dict[str, Any]) -> bool:
    diagnostic = source.get("connector_diagnostic") or {}
    if diagnostic.get("category") == "browser_session":
        return True
    raw_payload = source.get("raw_payload") or {}
    diagnostic = raw_payload.get("connector_diagnostic") or {}
    if diagnostic.get("category") == "browser_session":
        return True
    summary = str(source.get("error_summary") or "").lower()
    return any(p in summary for p in ("browser bridge", "browser session", "browser_connect"))


def _has_persistent_browser_failure(base_dir: Any, source_id: str) -> bool:
    """Browser failure is persistent if 2+ consecutive failures or failing > 1 hour."""
    from backend.infrastructure.storage.repositories import RunRepository
    repo = RunRepository(base_dir)
    runs = repo.list_runs(source_id=source_id)

    # Check consecutive failures
    consecutive = 0
    for run in runs:
        if run.get("status") == "success":
            break
        consecutive += 1
    if consecutive >= 2:
        return True

    # Check if failing for more than 1 hour
    if runs:
        first = runs[-1]
        last = runs[0]
        from datetime import datetime, timedelta
        try:
            first_time = datetime.fromisoformat(str(first.get("finished_at") or "").replace("Z", "+00:00"))
            last_time = datetime.fromisoformat(str(last.get("finished_at") or "").replace("Z", "+00:00"))
            if (last_time - first_time) > timedelta(hours=1) and consecutive > 0:
                return True
        except (ValueError, TypeError):
            pass

    return False


def check_and_notify(
    base_dir,
    *,
    cards_result=None,
    topics_result=None,
    cards=None,
    daily_brief_builder: Callable[[Path], dict[str, Any]] | None = None,
):
    """Check notification rules and send emails if needed. Best-effort."""
    try:
        config = get_config()
    except (OSError, ValueError):
        logger.exception("notification config unavailable")
        return {"status": "config_error"}

    if not config.get("enabled"):
        return {"status": "disabled"}

    if not config.get("email", {}).get("username"):
        return {"status": "not_configured"}

    rules = config.get("rules", {})
    min_score = rules.get("high_priority_min_score", 4)
    hp_cooldown = rules.get("high_priority_cooldown_minutes", 30)
    base_path = Path(base_dir)
    
    results = []

    # High-priority card notification
    if cards and is_due("high_priority", hp_cooldown, base_path):
        high_priority = []
        for card in cards[:50]:  # Check latest 50 cards
            scores = (card.get("scores") or {})
            ts = int(scores.get("timeliness_score", scores.get("timeliness", 0)) or 0)
            ins = int(scores.get("insight_score", scores.get("insight", 0)) or 0)
            if ts >= min_score or ins >= min_score:
                high_priority.append(card)

        if high_priority:
            subject, body = format_high_priority_email(high_priority)
            sent = send_email(subject=subject, body=body, config=config)
            if sent:
                record_sent("high_priority", base_path)
                results.append({"type": "high_priority", "cards": len(high_priority), "sent": True})
            else:
                results.append({"type": "high_priority", "cards": len(high_priority), "sent": False})

    # Source failure notification
    if cards_result:
        failure_cooldown = rules.get("source_failure_cooldown_hours", 6)
        sources = cards_result.get("sources", []) if isinstance(cards_result, dict) else []
        sources = _latest_source_records(sources)
        for source in sources:
            if source.get("status") not in ("success", "blocked"):
                sid = source.get("source_id", "unknown")
                rule_name = f"source_failure_{sid}"
                if is_due(rule_name, failure_cooldown * 60, base_path):
                    if _is_browser_session_failure(source):
                        if not _has_persistent_browser_failure(base_path, sid):
                            continue
                    error = source.get("error_summary", source.get("connector_diagnostic", {}).get("summary", "Unknown error"))
                    subject, body = format_source_failure_email(sid, str(error))
                    sent = send_email(subject=subject, body=body, config=config)
                    if sent:
                        record_sent(rule_name, base_path)
                    results.append({"type": "source_failure", "source_id": sid, "sent": sent})

    # Daily brief notification
    if daily_brief_builder is not None and is_due("daily_brief", 24 * 60, base_path) and is_daily_brief_time(config):
        try:
            brief = daily_brief_builder(base_path)
            subject, body = format_daily_brief_email(brief)
            sent = send_email(subject=subject, body=body, config=config)
            if sent:
                record_sent("daily_brief", base_path)
            results.append({"type": "daily_brief", "sent": sent})
        except Exception:  # noqa: BLE001 - Optional brief builder and SMTP delivery must not abort notification processing
            logger.exception("daily brief notification failed")
            results.append({"type": "daily_brief", "sent": False, "error": "brief_build_failed"})

    return {"status": "processed", "results": results}
