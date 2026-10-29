"""Main reader projections, acquisition counters and browser mutation security."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import cast

import pytest

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.application.usecases.opportunity_follow_up import set_opportunity_follow_up
from backend.infrastructure.storage.database import default_database_path
from tests.infoos.test_opportunity_follow_up import prepared
from tests.infoos.test_verifications import save as verify


def test_board_detail_stats_and_cross_site_put(tmp_path, monkeypatch):
    prepared(tmp_path)
    app = create_app(tmp_path)
    with TestClient(app) as client:
        board = client.get("/api/opportunities/board").json()
        assert board["columns"]["inbox"]["count"] == 1
        tile = board["columns"]["inbox"]["items"][0]
        assert tile["risk_flags"] == [] and tile["question_count"] == 1
        assert client.get("/api/opportunities/board?query=不匹配").json()["columns"]["inbox"]["count"] == 0
        assert client.get("/api/opportunities/board?inbox_offset=1").json()["columns"]["inbox"]["items"] == []
        path = "/api/opportunities/card-1/follow-up"
        assert client.put(path, json={"state": "following"}, headers={"Origin": "https://evil.test"}).status_code == 403
        first = client.put(path, json={"state": "following"}, headers={"Origin": "http://testserver"})
        assert first.status_code == 200 and first.json()["stage"] == "plan"
        assert client.put(path, json={"state": "following"}, headers={"Origin": "http://testserver"}).json() == first.json()
        assert client.put(path, json={"state": "action"}, headers={"Origin": "http://testserver"}).json()["code"] == "invalid_follow_up_state"
        verify(tmp_path, conclusion="keep_observing", doubts="仍需验证交付")
        detail = client.get("/api/opportunities/card-1").json()
        assert detail["stage"] == "contact"
        assert [entry["kind"] for entry in detail["timeline"]] == ["breakdown", "follow_up", "verification"]
        assert detail["labels"]["monetization_model"] == "课程/培训"
        stats = client.get("/api/opportunities/stats").json()
        assert stats["funnel"]["broken_down"] == stats["funnel"]["following"] == stats["funnel"]["contacted"] == 1
        assert stats["follow_up"]["stages"]["contact"] == 1
        assert stats["follow_up"]["median_contact_days"] >= 0
        assert stats["risks"][3]["count"] == 1
        assert client.get("/api/opportunities/missing").status_code == 404
        assert client.get("/api/opportunities/board?inbox_limit=0").status_code == 422
        assert client.get("/api/opportunities/stats?window=bad").status_code == 422
    set_opportunity_follow_up(tmp_path, "card-1", "following", actor="web")
    # A new decision after contact must append even if an older event had the same state.
    from backend.read_models.opportunity_board import board as read_board
    assert read_board(tmp_path)["columns"]["plan"]["count"] == 1
    set_opportunity_follow_up(tmp_path, "card-1", "reset", actor="web")
    assert read_board(tmp_path)["columns"]["contact"]["count"] == 1


def test_stats_historical_payload_and_shanghai_day(tmp_path, monkeypatch):
    prepared(tmp_path)
    from backend.read_models import opportunity_stats
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 22, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(opportunity_stats, "datetime", Clock)
    with sqlite3.connect(default_database_path(tmp_path)) as conn:
        source = "stats-source"
        conn.execute("insert into sources(source_id,platform,source_type,identifier,display_name,cadence_hours,"
            "connector_status,created_at,updated_at) values(?, 'douyin','home','home','抖音',1,'ready',?,?)",
            (source, "2026-10-01T00:00:00Z", "2026-10-01T00:00:00Z"))
        for index, (stamp, payload) in enumerate([
            ("2026-10-14T15:59:59Z", {"prefilter_summary": {"checked": 999, "matched": 999}}),
            ("2026-10-15T16:00:00Z", {"prefilter_summary": {"checked": 20, "matched": 3, "unknown": 2}}),
            ("2026-10-21T18:00:00Z", {"prefilter_summary": {}}),
            ("2026-10-21T18:00:00Z", {}),
            ("2026-10-21T18:00:00Z", {"prefilter_summary": None}),
        ]):
            conn.execute("insert into run_history(run_id,source_id,scheduled_for,started_at,finished_at,status,"
                "visible_count,trigger,raw_payload) values(?,?,?,?,?,'success',30,'test',?)",
                (f"run-{index}", source, stamp, stamp, stamp, json.dumps(payload)))
    stats = opportunity_stats.stats(tmp_path, "7d")
    assert stats["funnel"]["checked"] == 20 and stats["funnel"]["matched"] == 5
    assert stats["funnel"]["visible"] == 60
    assert [day["date"] for day in stats["daily"]] == [f"2026-10-{n}" for n in range(16, 23)]


def test_board_sum_order_observe_filters_and_current_profile_candidates(tmp_path):
    from backend.read_models.opportunity_board import board
    from backend.read_models.opportunity_stats import stats
    from tests.infoos.test_daily_intelligence import _card
    from tests.infoos.test_opportunity_breakdowns import mark, save
    from tests.infoos.test_opportunity_domain import item
    prepared(tmp_path)
    for number, rep, cred, step in [(2, 90, 5, "verify"), (3, 70, 80, "verify"), (4, 50, 40, "observe")]:
        _card(default_database_path(tmp_path), number, f"2026-10-{number:02}T00:00:00Z", evaluated=False)
        mark(tmp_path, number)
        save(tmp_path, [item(f"card-{number}", replicability=rep, credibility=cred,
                             next_step=step, verify_questions=["交付是什么？"])])
    page = board(tmp_path, inbox_limit=2)
    assert [row["card_id"] for row in page["columns"]["inbox"]["items"]] == ["card-3", "card-1"]
    assert page["columns"]["inbox"]["count"] == 3
    assert board(tmp_path, inbox_offset=2)["columns"]["inbox"]["items"][0]["card_id"] == "card-2"
    assert board(tmp_path, include_observe=True)["columns"]["inbox"]["count"] == 4
    assert board(tmp_path, monetization_model="ecommerce")["columns"]["inbox"]["count"] == 0
    assert stats(tmp_path)["progress"]["pending"] == 0
    with sqlite3.connect(default_database_path(tmp_path)) as conn:
        conn.execute("update raw_items set revision=revision+1 where raw_item_id='card-3'")
    assert stats(tmp_path)["progress"]["pending"] == 1


@pytest.fixture(scope="module")
def board_base(tmp_path_factory):
    from backend.infrastructure.storage.bootstrap import initialize_runtime_storage
    from tests.infoos.test_agent_evaluations import seed
    from tests.infoos.test_daily_intelligence import _card
    from tests.infoos.test_opportunity_breakdowns import mark, save
    from tests.infoos.test_opportunity_domain import item

    base = tmp_path_factory.mktemp("board-two-phase")
    initialize_runtime_storage(base)
    seed(base)
    db = default_database_path(base)
    with sqlite3.connect(db) as conn:
        conn.execute("insert into sources(source_id,platform,source_type,identifier,display_name,cadence_hours,"
            "connector_status,created_at,updated_at) values('wind','zsxq','opportunity','wind','风向标',1,'ready','','')")
    for number in range(1, 36):
        stamp = f"2026-10-21T{number % 24:02}:00:00Z"
        if number != 1:
            _card(db, number, stamp, content_type="case")
        platform = ("x", " X ", "X")[number - 1] if number <= 3 else ("rss", "douyin", "xiaohongshu", "zsxq")[number % 4]
        title, content = f"Stored headline {number}", f"Ordinary body {number}"
        if number == 1:
            title, content = "Hello", "Hello,\nDisplayed repair alpha is the useful title."
        elif number == 2:
            title, content = "https://t.", "https://t.co/xyz\nDisplayed repair beta is another useful title."
        record = {"author_url": f"https://example.test/author/{number}"} if number <= 3 else {
            "sec_uid": "douyin/id", "author_user_id": "xiaohongshu/id"}
        if number % 3 == 0:
            mark(base, number)
        elif number % 3 == 1:
            with sqlite3.connect(db) as conn:
                conn.execute("update raw_items set source_id='wind' where raw_item_id=?", (f"card-{number}",))
        with sqlite3.connect(db) as conn:
            conn.execute("update cards set source_platform=?,title=?,content=?,raw_payload=?,source_id=?,"
                "source_author=?,published_at=? where card_id=?",
                (platform, title, content, json.dumps({"raw_payload": {"opencli_record": record}}),
                 "wind" if number % 3 == 1 else None, None if number % 5 == 0 else f"Author {number}",
                 stamp, f"card-{number}"))
        step = "observe" if number == 27 else "skip" if number in (28, 34) else "verify"
        assert save(base, [item(f"card-{number}", next_step=step, verify_questions=["交付是什么？"],
            replicability=number % 5 * 20, credibility=100 - number % 5 * 20,
            risk_flags=["unverifiable", "platform_risk"],
            monetization_model=("course", "service", "ecommerce")[number % 3])])[0]["status"] == "accepted"
        with sqlite3.connect(db) as conn:
            conn.execute("update opportunity_breakdowns set created_at=? where card_id=?", (stamp, f"card-{number}"))
    for number, state in [(29, "following"), (32, "dismissed")]:
        set_opportunity_follow_up(base, f"card-{number}", state, actor="web")
    for number, conclusion in [(30, "keep_observing"), (31, "to_action"), (33, "drop")]:
        verify(base, card_id=f"card-{number}", conclusion=conclusion, doubts="需要核实")
    with sqlite3.connect(db) as conn:
        conn.execute("update opportunity_follow_up_events set created_at='2026-10-01T00:00:00Z'")
        conn.execute("update opportunity_verifications set created_at='2026-10-01T00:00:00Z'")
    return base


@pytest.fixture
def board_clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 22, 1, tzinfo=timezone.utc)
    monkeypatch.setattr("backend.read_models.opportunity_board.datetime", Clock)
    monkeypatch.setattr("backend.domain.intelligence.opportunity_follow_up.datetime", Clock)
    monkeypatch.setitem(globals(), "datetime", Clock)


def reference_items(base, include_observe=False, *, now=None, include_unstaged=False):
    """Load all display fields in one query, as the original board did."""
    from backend.infrastructure.storage.base import InfoOSRepository
    from backend.infrastructure.storage.card_entry import card_display_fields
    from backend.read_models.opportunity_board import board_items
    from backend.read_models.opportunity_board_data import LATEST_BREAKDOWN, author_profile_url

    with InfoOSRepository(base).connect_existing() as conn:
        rows = conn.execute("select b.card_id,c.title,c.source_platform,c.content,c.raw_payload "
            "from opportunity_breakdowns b join cards c on c.card_id=b.card_id where " + LATEST_BREAKDOWN).fetchall()
    display = {}
    for row in rows:
        payload = json.loads(row["raw_payload"])
        display[row["card_id"]] = {**card_display_fields(row["source_platform"], row["title"], row["content"], payload),
            "author_url": author_profile_url(row["source_platform"], payload)}
    items = board_items(base, include_observe, now=now, include_unstaged=include_unstaged)
    for entry in items:
        entry["title"] = display[entry["card_id"]]["title"]
        entry["author_url"] = display[entry["card_id"]]["author_url"]
    return items


# Preserve the original filtering, ordering, counts, pagination and response key order.
from backend.domain.intelligence.opportunity import CandidateKind, ENUM_MEANINGS
from backend.domain.intelligence.opportunity_follow_up import STAGES
from backend.domain.timestamps import parse_utc
from backend.read_models.cards import platform_display_label
from backend.read_models.opportunity_board_types import BoardColumn, BoardData, OpportunityPage


def reference_board(base_dir: Path | str, platform: str | None = None, monetization_model: str | None = None,
          query: str | None = None, include_observe: bool = False, inbox_limit: int = 20,
          inbox_offset: int = 0, candidate_kind: CandidateKind | None = None) -> BoardData:
    base_dir = Path(base_dir)
    now = datetime.now(timezone.utc)
    items = reference_items(base_dir, include_observe, now=now)
    platforms = sorted({item["platform"] for item in items})
    filtered = [item for item in items if (platform is None or item["platform"] == platform)
        and (candidate_kind is None or item["candidate_kind"] == candidate_kind)
        and (monetization_model is None or item["monetization_model"] == monetization_model)
        and (query is None or query.casefold() in " ".join(
            str(item[field]) for field in ("card_id", "title", "offer", "author", "source_name") if item[field] is not None).casefold())]
    columns: dict[str, BoardColumn] = {}
    for stage in STAGES:
        group = [item for item in filtered if item["stage"] == stage]
        if stage == "inbox":
            group.sort(key=lambda item: (item["replicability"] + item["credibility"],
                parse_utc(item["created_at"]), item["card_id"]), reverse=True)
        else:
            group.sort(key=lambda item: (parse_utc(cast(str, item["stage_since"])), item["card_id"]), reverse=True)
        columns[stage] = {"count": len(group), "items": group[inbox_offset:inbox_offset + inbox_limit]
            if stage == "inbox" else group}
    return {"columns": columns, "today": {
        "inbox": sum(item["stage"] == "inbox" and now - parse_utc(item["created_at"]) < timedelta(days=1)
                     for item in filtered),
        "plan_stale": sum(item["stage"] == "plan" and item["stale"] for item in filtered),
        "contact": columns["contact"]["count"], "action": columns["action"]["count"]},
        "platforms": [{"platform": value, "label": platform_display_label(value)} for value in platforms],
        "models": ENUM_MEANINGS["monetization_model"]}


def reference_all_opportunities(base_dir: Path | str, *, platform: str | None = None,
                      monetization_model: str | None = None, query: str | None = None,
                      next_step: str | None = None, stage: str | None = None,
                      candidate_kind: CandidateKind | None = None, limit: int = 20, offset: int = 0) -> OpportunityPage:
    items = reference_items(Path(base_dir), True, include_unstaged=True)
    platforms = sorted({item["platform"] for item in items})
    filtered = [item for item in items if (platform is None or item["platform"] == platform)
        and (monetization_model is None or item["monetization_model"] == monetization_model)
        and (next_step is None or item["next_step"] == next_step)
        and (candidate_kind is None or item["candidate_kind"] == candidate_kind)
        and (stage is None or (item["stage"] is None if stage == "unstaged" else item["stage"] == stage))
        and (query is None or query.casefold() in " ".join(str(item[field])
            for field in ("card_id", "title", "offer", "author", "source_name") if item[field] is not None).casefold())]
    filtered.sort(key=lambda item: (parse_utc(item["created_at"]), item["card_id"]), reverse=True)
    return {"items": filtered[offset:offset + limit], "total": len(filtered), "limit": limit, "offset": offset,
        "platforms": [{"platform": value, "label": platform_display_label(value)} for value in platforms],
        "models": ENUM_MEANINGS["monetization_model"]}


def test_board_matches_original_bytes(board_base, board_clock):
    from backend.read_models.opportunity_board import board
    for options in [
        {}, {"inbox_offset": 20}, {"inbox_offset": 100}, {"include_observe": True},
        {"platform": "x"}, {"platform": "rss"}, {"monetization_model": "course"},
        *[{"candidate_kind": kind} for kind in ("prefilter", "wind_vane", "case")],
        {"query": "Displayed repair alpha"}, {"query": "Hello"}, {"query": "https://t."}, {"query": ""},
        {"query": "AUTHOR"}, {"query": "风向标"}, {"query": "培训课程"},
        {"platform": "x", "query": "Displayed", "monetization_model": "service", "candidate_kind": "wind_vane"},
    ]:
        assert json.dumps(board(board_base, **options), ensure_ascii=False) == json.dumps(
            reference_board(board_base, **options), ensure_ascii=False), options


def test_all_opportunities_matches_original_bytes(board_base, board_clock):
    from backend.read_models.opportunity_board import all_opportunities
    for options in [
        {}, {"offset": 20}, {"limit": 4, "offset": 2}, {"offset": 100},
        *[{"stage": stage} for stage in (*STAGES, "unstaged")],
        *[{"next_step": step} for step in ("verify", "observe", "skip")],
        *[{"candidate_kind": kind} for kind in ("prefilter", "wind_vane", "case")],
        {"platform": "x"}, {"monetization_model": "ecommerce"},
        {"query": "Displayed repair alpha"}, {"query": "Hello"}, {"query": "https://t."}, {"query": ""},
        {"query": "AUTHOR"}, {"query": "风向标"}, {"query": "培训课程"},
        {"query": "Displayed", "platform": "x", "monetization_model": "service", "stage": "inbox", "next_step": "verify"},
    ]:
        assert json.dumps(all_opportunities(board_base, **options), ensure_ascii=False) == json.dumps(
            reference_all_opportunities(board_base, **options), ensure_ascii=False), options


def test_reference_display_and_stage_coverage(board_base, board_clock):
    from backend.domain.card_titles import broken_body_title
    from backend.read_models.opportunity_board import all_opportunities, board
    items = all_opportunities(board_base, limit=100)["items"]
    assert len(items) == 35
    assert {entry["stage"] for entry in items} == {*STAGES, None}
    assert {entry["candidate_kind"] for entry in items} == {"prefilter", "wind_vane", "case"}
    assert board(board_base)["columns"]["inbox"]["count"] > 20
    by_id = {entry["card_id"]: entry for entry in items}
    with sqlite3.connect(default_database_path(board_base)) as conn:
        for card_id in ("card-1", "card-2"):
            title, content = conn.execute("select title,content from cards where card_id=?", (card_id,)).fetchone()
            assert broken_body_title(title, content) and by_id[card_id]["title"] != title
    assert by_id["card-3"]["title"] == "Stored headline 3"
    assert by_id["card-1"]["author_url"] == "https://example.test/author/1"
    assert by_id["card-5"]["author_url"] == "https://www.douyin.com/user/douyin%2Fid"
    assert by_id["card-6"]["author_url"] == "https://www.xiaohongshu.com/user/profile/xiaohongshu%2Fid"
    assert all_opportunities(board_base, query="Displayed repair alpha")["total"] == 1
    assert all_opportunities(board_base, query="Hello")["total"] == 0
    assert all_opportunities(board_base, query="https://t.")["total"] == 0


def test_body_reads_only_for_x_and_urls_only_for_output(board_base, board_clock, monkeypatch):
    from backend.domain.card_titles import title_needs_body
    from backend.read_models import opportunity_board, opportunity_board_data
    original = opportunity_board_data._rows_by_card_id
    reads = []
    def record(conn, columns, card_ids):
        reads.append((columns, list(card_ids)))
        yield from original(conn, columns, card_ids)
    monkeypatch.setattr(opportunity_board_data, "_rows_by_card_id", record)
    with sqlite3.connect(default_database_path(board_base)) as conn:
        x_ids = {card_id for card_id, platform in conn.execute("select card_id,source_platform from cards")
                 if title_needs_body(platform)}
    for read, options in [(opportunity_board.board, {}),
                          (opportunity_board.board, {"query": "Displayed"}),
                          (opportunity_board.all_opportunities, {"limit": 2, "offset": 1}),
                          (opportunity_board.all_opportunities, {"query": "", "limit": 2}),
                          (opportunity_board.all_opportunities, {"platform": "rss"})]:
        reads.clear()
        result = read(board_base, **options)
        shown = result["items"] if "items" in result else [
            entry for column in result["columns"].values() for entry in column["items"]]
        for columns, ids in reads:
            if columns == "source_platform,title,content":
                assert set(ids) <= x_ids
                if "query" not in options:
                    assert set(ids) == {entry["card_id"] for entry in shown} & x_ids
        assert [card_id for columns, ids in reads if columns == "source_platform,raw_payload" for card_id in ids] == [
            entry["card_id"] for entry in shown]
