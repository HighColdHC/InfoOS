"""Knowledge brief sections, frozen snapshots, late writes and digest."""
import sqlite3

from backend.application.usecases.daily_brief import build_daily_brief
from backend.domain.intelligence.brief_digest import format_brief_digest
from backend.domain.intelligence.daily_brief import select_knowledge_brief, stock_line
from backend.infrastructure.storage.daily_briefs import DailyBriefRepository
from backend.infrastructure.storage.database import default_database_path
from tests.infoos.test_knowledge_cards import library as library
from tests.infoos.test_knowledge_cards import seed, extract, SINCE
from tests.infoos.test_knowledge_domain import knowledge


def row(kind, number, **changes):
    return {"knowledge_id": f"kc_{kind}_{number}", "kind": kind, "subtype": "hot" if kind == "topic" else "ai",
        "title": f"标题{number}", "use_note": "用处" * 50, "attribution_kind": "project", "attribution_ref": "InfoOS",
        "strength": number, "origin_freshness": "fresh", "created_at": "2026-10-27T12:00:00.000000Z",
        "is_new": True, "is_update": False, "origin_card_id": "origin", **changes}


def test_sections_caps_sort_stock_and_update():
    rows = [row(kind, n) for kind in ("news", "opportunity", "project", "topic", "method") for n in range(1, 9)]
    rows += [row("project", 99, origin_freshness="stock"), row("opportunity", 99, origin_freshness="stock"),
             row("project", 0, attribution_kind="none", strength=100),
             row("method", 99, is_new=False, is_update=True), row("news", 99, origin_freshness="recent")]
    brief = select_knowledge_brief(rows)
    assert brief["counts"] == {"news": 8, "opportunity": 8, "project": 9, "topic": 8, "deposit": 8}
    assert [len([i for i in brief["items"] if i["section"] == section]) for section in brief["counts"]] == [5, 5, 3, 5, 3]
    assert [i["number"] for i in brief["items"]] == list(range(1, 22))
    assert [i["strength"] for i in brief["items"] if i["section"] == "project"] == [8, 7, 6]
    assert [i for i in brief["items"] if i["section"] == "deposit"][0]["is_update"]
    assert stock_line(brief["stock"]) == "存量入库：项目 +1、商机 +1"
    assert select_knowledge_brief([row("topic", 1, subtype="borrowable", strength=100), row("topic", 2)])[
        "items"][0]["subtype"] == "hot"
    title, body = format_brief_digest({"brief_date": "2026-10-28", **brief}, "/r/brief/2026-10-28")
    assert title == "InfoOS 日报 10-28｜商机 8 · 项目 9 · 选题 8"
    assert "存量入库：项目 +1、商机 +1" in body
    assert "（更新）" in body and "/r/knowledge/kc_method_99?from=feishu" in body
    assert "用处" * 30 in body and "用处" * 31 not in body


def test_freeze_late_extraction_update_and_old_snapshot(library, monkeypatch):
    base = library
    seed(base, 1)
    seed(base, 2)
    seed(base, 3)
    monkeypatch.setattr("backend.application.usecases.submit_knowledge_extractions.utc_now_micro_z",
                        lambda: "2026-10-27T22:00:00.000000Z")
    first = extract(base, "card-1")
    target = first["cards"][0]["knowledge_id"]
    brief = build_daily_brief(base, "2026-10-28")["brief"]
    assert brief["layout"] == "knowledge_v1" and brief["counts"]["project"] == 1
    monkeypatch.setattr("backend.application.usecases.submit_knowledge_extractions.utc_now_micro_z",
                        lambda: "2026-10-28T01:00:00.000000Z")
    assert extract(base, "card-2", [knowledge(action="update", target_knowledge_id=target, note="新结果")])["status"] == "accepted"
    assert extract(base, "card-3", [knowledge("method")])["status"] == "accepted"
    assert build_daily_brief(base, "2026-10-28")["brief"] == brief
    tomorrow = build_daily_brief(base, "2026-10-29")["brief"]
    assert tomorrow["counts"]["project"] == 0 and tomorrow["counts"]["deposit"] == 1
    assert [i["knowledge_id"] for i in tomorrow["items"] if i["is_update"]] == [target]
    assert len(tomorrow["items"]) == 2
    # Persisted old object/list snapshots are read without migrating their structure.
    import json
    for day, snapshot in (("2026-10-20", {"items": [{"number": 1, "card_id": "old"}], "counts": {"related": 1}}),
                          ("2026-10-21", [{"number": 1, "card_id": "older"}])):
        with sqlite3.connect(default_database_path(base)) as conn:
            conn.execute("""insert into daily_briefs (brief_date,window_start,window_end,profile_version,items_json,generated_at)
                values (?,?,?,1,?,?)""", (day, SINCE, "2026-10-28T00:00:00Z", json.dumps(snapshot), SINCE))
        old = DailyBriefRepository(base).get(day)
        assert "layout" not in old and old["items"][0]["card_id"] in ("old", "older")
