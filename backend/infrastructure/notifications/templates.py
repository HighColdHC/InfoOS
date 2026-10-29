from __future__ import annotations

from backend.domain.timestamps import utc_now_display_minutes


def format_high_priority_email(cards: list[dict], base_url: str = "http://127.0.0.1:8000") -> tuple[str, str]:
    """Return (subject, body) for high-priority cards email."""
    count = len(cards)
    preview = cards[0]["title"][:50] if cards else "new signals"
    subject = f"InfoOS — [{count} high-priority signal{'s' if count > 1 else ''}] {preview}"

    lines = [f"🚀 {count} High-Priority Signal{'s' if count > 1 else ''}", "━" * 40]
    for card in cards:
        title = card.get("title", "")[:120]
        source = card.get("source_platform", "") or card.get("source", "unknown")
        url = card.get("source_url", "")
        insight = card.get("ai_insight", {}) if isinstance(card.get("ai_insight"), dict) else {}
        why = insight.get("why_it_matters", "")
        scores = insight.get("scores", {}) or card.get("scores", {})

        lines.append(f"\n{title}")
        lines.append(f"  Source: {source} | T: ★{scores.get('timeliness','?')} I: ★{scores.get('insight','?')}")
        if why:
            lines.append(f"  → {why[:150]}")
        if url:
            lines.append(f"  🔗 {url[:100]}")

    lines.extend(["", "━" * 40, f"🔗 Open InfoOS: {base_url}", "", f"Sent: {utc_now_display_minutes()}"])
    return subject, "\n".join(lines)


def format_daily_brief_email(brief: dict, base_url: str = "http://127.0.0.1:8000") -> tuple[str, str]:
    """Return (subject, body) for daily brief email."""
    topic_count = brief.get("topic_count", 0)
    card_count = brief.get("card_count", 0)
    subject = f"InfoOS Daily Brief — {topic_count} topics, {card_count} cards"

    lines = [
        "📊 InfoOS Daily Brief",
        "━" * 40,
        f"Topics: {topic_count} | Cards: {card_count}",
        "",
    ]

    topics = brief.get("topics", [])[:8]
    if topics:
        lines.append("📌 Top Topics:")
        for t in topics:
            title = t.get("title", "?")[:60]
            count = t.get("card_count", t.get("related_card_count", "?"))
            lines.append(f"  • {title} ({count} cards)")

    high_priority = brief.get("high_priority_signals", [])[:5]
    if high_priority:
        lines.append("\n⚠️ High Priority Signals:")
        for s in high_priority:
            lines.append(f"  • {s.get('title', '?')[:80]}")

    lines.extend(["", "━" * 40, f"🔗 Open InfoOS: {base_url}", "", f"Sent: {utc_now_display_minutes()}"])
    return subject, "\n".join(lines)


def format_source_failure_email(source_id: str, error: str, base_url: str = "http://127.0.0.1:8000") -> tuple[str, str]:
    """Return (subject, body) for source failure alert."""
    subject = f"InfoOS ⚠️ Source failure: {source_id}"
    body = f"Source: {source_id}\nError: {error[:200]}\n\n🔗 {base_url}\n\nSent: {utc_now_display_minutes()}"
    return subject, body
