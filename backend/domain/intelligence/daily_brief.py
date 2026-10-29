"""Pure knowledge-brief selection and missing-profile retry boundaries."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from backend.domain.timestamps import format_utc_seconds_z

SHANGHAI = ZoneInfo("Asia/Shanghai")


def profile_retry_at(brief_date: str, now: datetime) -> str | None:
    day = date.fromisoformat(brief_date)
    deadline = datetime(day.year, day.month, day.day, 23, 59, tzinfo=SHANGHAI)
    if now.astimezone(SHANGHAI) > deadline:
        return None
    return format_utc_seconds_z(now.astimezone(timezone.utc) + timedelta(minutes=30))


# Knowledge briefs count new cards before caps, and include existing cards updated in-window.
KNOWLEDGE_SECTIONS = {"news": "资讯", "opportunity": "商机", "project": "项目", "topic": "选题", "deposit": "沉淀"}
ATTRIBUTION_ORDER = {"project": 0, "kr": 1, "question": 2, "domain": 3, "none": 4}


def select_knowledge_brief(rows: list[dict]) -> dict:
    from backend.domain.intelligence.knowledge_kinds import KINDS
    counts = dict.fromkeys(KNOWLEDGE_SECTIONS, 0)
    stock = {key: 0 for key in KINDS}
    available: dict[str, list[dict]] = {key: [] for key in KINDS}
    for row in rows:
        kind = KINDS[row["kind"]]
        if row["origin_freshness"] == "stock":
            stock[kind.key] += int(row["is_new"])
            continue
        if not kind.enabled or (kind.key == "news" and row["origin_freshness"] != "fresh"):
            continue
        counts[kind.brief_section] += int(row["is_new"])
        available[kind.key].append(row)
    items = []
    for section in KNOWLEDGE_SECTIONS:
        for kind in KINDS.values():
            if not kind.enabled or kind.brief_section != section:
                continue
            ordered = sorted(available[kind.key], key=lambda row: (
                (0 if row["subtype"] == "hot" else 1) if kind.key == "topic" else 0,
                ATTRIBUTION_ORDER[row["attribution_kind"]], -row["strength"], row["created_at"], row["knowledge_id"]))
            for row in ordered[:kind.brief_cap]:
                items.append({"number": len(items) + 1, "section": section,
                    **{key: row[key] for key in ("knowledge_id", "kind", "subtype", "title", "use_note", "attribution_ref", "strength")},
                    "is_update": bool(row["is_update"]) and not bool(row["is_new"]),
                    "deep_link": f"/r/knowledge/{row['knowledge_id']}", "origin_card_link": f"/r/card/{row['origin_card_id']}"})
    return {"layout": "knowledge_v1", "items": items, "counts": counts, "stock": stock, "weekly_paused": None}


def stock_line(stock: dict[str, int]) -> str:
    from backend.domain.intelligence.knowledge_kinds import KINDS
    parts = [f"{kind.label} +{stock[kind.key]}" for kind in KINDS.values() if stock[kind.key]]
    return "存量入库：" + ("、".join(parts) if parts else "无")
