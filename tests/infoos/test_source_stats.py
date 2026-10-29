from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from backend.domain.intelligence.source_stats import source_verdict
from backend.infrastructure.storage.source_stats import aggregate_source_stats
from backend.infrastructure.storage.sources import SourceRepository
from backend.read_models.source_stats import get_source_stats
from tests.infoos.test_daily_intelligence import _card, _db

START = "2026-09-01T00:00:00Z"
END = "2026-10-01T00:00:00Z"


def _source(base, source_id, *, enabled=True):
    SourceRepository(base).upsert_source({
        "source_id": source_id, "platform": "rss", "source_type": "feed",
        "identifier": f"https://example.test/{source_id}", "display_name": f"Feed {source_id}",
        "cadence_hours": 6, "enabled": enabled, "connector_status": "ready",
    })


def _source_card(db, number, source_id, entered=START, **kwargs):
    _card(db, number, entered, **kwargs)
    with sqlite3.connect(db) as conn:
        # Deliberately different raw attribution: grouping belongs to cards.source_id.
        conn.execute("update cards set source_id=? where card_id=?", (source_id, f"card-{number}"))


def test_aggregate_latest_window_active_and_source_metadata(tmp_path):
    db = _db(tmp_path)
    _source(tmp_path, "a", enabled=False)
    _source(tmp_path, "b")
    _source(tmp_path, "empty")
    _source_card(db, 1, "a", quality=20, passed=0)
    _source_card(db, 2, "a", evaluated=False)
    _source_card(db, 3, "a", END)
    _source_card(db, 4, "a", "2026-08-31T23:59:59Z")
    _source_card(db, 5, "a")
    _source_card(db, 6, "b", kind="paused", relevance=None, quality=61)
    _source_card(db, 7, "b", kind="exploration", relevance=None, quality=80)
    with sqlite3.connect(db) as conn:
        conn.execute("update cards set status='withdrawn' where card_id='card-5'")
        # A later timestamp wins, even when its evaluation ID sorts lower.
        conn.execute("""insert into card_evaluations (evaluation_id,card_id,card_version,profile_version,prompt_version,model,provider_id,
            quality,relevance,attribution_kind,attribution_ref,content_type,suggested_action,paused_landmark,
            summary,relation_note,passed_quality,receipt_id,created_at) select
            'aaa-later', card_id, card_version, profile_version, 'later', model, provider_id,
            90, 70, 'exploration', attribution_ref, content_type, suggested_action,
            paused_landmark, summary, relation_note, 1, receipt_id, '2026-10-02T00:00:00.000000Z'
            from card_evaluations where evaluation_id='eval-1'""")
        # Same timestamp is resolved by descending evaluation_id.
        conn.execute("""insert into card_evaluations (evaluation_id,card_id,card_version,profile_version,prompt_version,model,provider_id,
            quality,relevance,attribution_kind,attribution_ref,content_type,suggested_action,paused_landmark,
            summary,relation_note,passed_quality,receipt_id,created_at) select
            'zzz-tie', card_id, card_version, profile_version, 'tie', model, provider_id,
            91, 71, 'paused', attribution_ref, content_type, suggested_action,
            paused_landmark, summary, relation_note, 1, receipt_id, created_at
            from card_evaluations where evaluation_id='aaa-later'""")
    result = get_source_stats(tmp_path, "2026-09-01T02:00:00.999+02:00", END, 1)
    assert result["since"] == START
    assert result["until"] == END
    assert result["quality_threshold"] == 60
    assert result["min_evaluated"] == 1
    rows = {row["source_id"]: row for row in result["items"]}
    assert set(rows) == {"a", "b"}
    assert rows["a"] == {
        "source_id": "a", "display_name": "Feed a", "platform": "rss", "enabled": False,
        "cadence_hours": 6.0, "cards": 2, "evaluated": 1, "passed": 1,
        "avg_quality": 91.0, "avg_relevance": 71.0, "paused": 1, "exploration": 0,
        "pass_rate": 1.0, "coverage": 0.5, "verdict": "high_yield",
        "fetch_run_count": 0, "partial_run_count": 0, "partial_run_rate": None, "latest_stop_reason": None,
    }
    assert rows["b"]["avg_relevance"] is None
    assert rows["b"]["avg_quality"] == 70.5
    assert rows["b"]["paused"] == rows["b"]["exploration"] == 1
    assert result["totals"] == {"cards": 4, "evaluated": 3, "passed": 3, "pass_rate": 1.0}
    assert aggregate_source_stats(tmp_path, START, START) == []


@pytest.mark.parametrize("evaluated,rate,minimum,expected", [
    (9, 0.0, 10, "insufficient_data"), (10, 0.099, 10, "low_yield"),
    (10, 0.10, 10, "normal"), (10, 0.399, 10, "normal"),
    (10, 0.40, 10, "high_yield"), (0, None, 1, "insufficient_data"),
])
def test_verdict_boundaries(evaluated, rate, minimum, expected):
    assert source_verdict(evaluated, rate, minimum) == expected


def test_partial_runs_use_finish_window_and_include_sources_without_cards(tmp_path):
    from backend.infrastructure.storage.runs import RunRepository
    _db(tmp_path)
    _source(tmp_path, "feed")
    runs = RunRepository(tmp_path)
    for number, (finished, partial, reason, kind) in enumerate([
        (START, True, "no_progress", "fetch"),
        ("2026-09-02T00:00:00Z", True, "detached", "fetch"),
        ("2026-09-03T00:00:00Z", False, None, "fetch"),
        ("2026-09-04T00:00:00Z", None, None, "fetch"),
        (END, True, "skipped_position", "fetch"),
        ("2026-08-01T00:00:00Z", True, "unknown", "fetch"),
        ("2026-09-05T00:00:00Z", True, "ignored", "card_finalize"),
    ]):
        runs.append_run({"run_id": str(number), "source_id": "feed", "status": "success",
                         "scheduled_for": finished, "started_at": finished, "finished_at": finished,
                         "task_type": kind, "raw_payload": {} if partial is None else {
                             "completeness_status": "partial" if partial else "complete", "stop_reason": reason}})
    row = get_source_stats(tmp_path, START, END)["items"][0]
    assert row["fetch_run_count"] == 4
    assert row["partial_run_count"] == 2
    assert row["partial_run_rate"] == 0.5
    assert row["latest_stop_reason"] == "detached"
    assert row["cards"] == 0
    assert row["coverage"] is None
    assert row["verdict"] == "insufficient_data"


def test_sort_totals_and_unevaluated(tmp_path):
    db = _db(tmp_path)
    number = 0
    for source_id, count, passed in [("b", 10, 0), ("a", 10, 0), ("c", 20, 0),
                                     ("d", 2, 0), ("e", 10, 1), ("f", 10, 4), ("g", 1, None)]:
        _source(tmp_path, source_id)
        for i in range(count):
            number += 1
            _source_card(db, number, source_id, passed=int(i < (passed or 0)), evaluated=passed is not None)
    result = get_source_stats(tmp_path, START, END)
    assert [r["source_id"] for r in result["items"]] == ["c", "a", "b", "d", "e", "f", "g"]
    assert [r["verdict"] for r in result["items"]] == [
        "low_yield", "low_yield", "low_yield", "insufficient_data", "normal", "high_yield", "insufficient_data"]
    assert result["totals"] == {"cards": 63, "evaluated": 62, "passed": 5, "pass_rate": 0.081}
    last = result["items"][-1]
    assert last["avg_quality"] is last["avg_relevance"] is last["pass_rate"] is None
    assert last["coverage"] == 0
    empty = get_source_stats(tmp_path, END, END)
    assert empty["items"] == []
    assert empty["totals"] == {"cards": 0, "evaluated": 0, "passed": 0, "pass_rate": None}


@pytest.mark.parametrize("kwargs", [
    {"since": "2026-09-01"}, {"until": "bad"}, {"since": ""},
    {"since": END, "until": START}, {"min_evaluated": 0}, {"min_evaluated": 1001},
    {"min_evaluated": True}, {"min_evaluated": 1.5},
])
def test_invalid_parameters(tmp_path, kwargs):
    with pytest.raises(ValueError):
        get_source_stats(tmp_path, **kwargs)


def test_defaults_and_min_evaluated_limits(tmp_path):
    _db(tmp_path)
    before = datetime.now(timezone.utc).replace(microsecond=0)
    result = get_source_stats(tmp_path)
    after = datetime.now(timezone.utc).replace(microsecond=0)
    end = datetime.fromisoformat(result["until"].replace("Z", "+00:00"))
    start = datetime.fromisoformat(result["since"].replace("Z", "+00:00"))
    assert before <= end <= after
    assert end - start == timedelta(days=30)
    for minimum in (1, 1000):
        assert get_source_stats(tmp_path, START, END, minimum)["min_evaluated"] == minimum


def test_coverage_rounds_non_divisible_ratio(tmp_path):
    db = _db(tmp_path)
    for number in range(1, 4):
        _source_card(db, number, "a", evaluated=number == 1)
    assert get_source_stats(tmp_path, START, END)["items"][0]["coverage"] == 0.333
