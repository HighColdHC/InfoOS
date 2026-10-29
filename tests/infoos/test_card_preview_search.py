from contextlib import contextmanager
from datetime import datetime

import pytest

from backend.infrastructure.storage.repositories import CardContentBlockRepository
from backend.read_models.card_preview import read_card_preview_page
from tests.infoos.test_card_preview_api import _store_card, _set_card_publication


@pytest.mark.parametrize("query", [None, "   ", "nebula", "人工智能", "ne nebula", "nebula ne"])
def test_fts_count_and_page_plans_use_narrow_indexes(tmp_path, monkeypatch, query):
    from backend.infrastructure.storage.card_preview_query import read_preview_rows
    from backend.infrastructure.storage.cards import CardRepository

    for card_id, title in (("card", "nebula 人工智能"), ("block", "block match"), ("miss", "unrelated")):
        _store_card(tmp_path, card_id=card_id, platform="rss", title=title, author="作者")
    CardContentBlockRepository(tmp_path).upsert_block({
        "block_id": "body", "card_id": "block", "position": 0, "layer": "original",
        "block_kind": "article", "status": "preserved", "body": "nebula 人工智能",
    })
    plans = {}
    connect_existing = CardRepository.connect_existing

    @contextmanager
    def capture_plans(repo):
        with connect_existing(repo) as conn:
            statements = []
            conn.set_trace_callback(statements.append)
            try:
                yield conn
            finally:
                conn.set_trace_callback(None)
            for sql in statements:
                if sql.startswith("select count(*) from cards c"):
                    kind = "count"
                elif sql.startswith("select c.card_id from cards "):
                    kind = "page"
                else:
                    continue
                plans[kind] = [row[3].upper() for row in conn.execute("EXPLAIN QUERY PLAN " + sql)]

    monkeypatch.setattr(CardRepository, "connect_existing", capture_plans)
    rows, total = read_preview_rows(
        tmp_path, query=query, platform=None, completeness=None,
        status="active", sort="published_desc", limit=20, offset=0,
    )
    expected = {"card", "block"} if query and query.strip() else {"card", "block", "miss"}
    assert total == len(expected)
    assert {row["card_id"] for row in rows} == expected
    assert plans.keys() == {"count", "page"}
    for kind, details in plans.items():
        if not query or not query.strip():
            assert not any("FTS" in detail or "UNION" in detail for detail in details), (kind, details)
            if kind == "page":
                assert any("IDX_CARDS_STATUS_SORT" in detail for detail in details), details
                assert not any("TEMP B-TREE" in detail for detail in details), details
            continue
        if kind == "count":
            assert not any(
                "STATUS=?" in detail and any(index in detail for index in (
                    "IDX_CARDS_STATUS_PUBLISHED", "IDX_CARDS_STATUS_SORT",
                )) for detail in details
            ), (kind, details)
        else:
            assert any("SEARCH K USING COVERING INDEX IDX_CARDS_STATUS_SORT (STATUS=?)" in detail
                       for detail in details), details
            assert not any("USE TEMP B-TREE FOR" in detail for detail in details), details
        assert any("CARD_TEXT_FTS" in detail for detail in details), (kind, details)
        assert any("CARD_BLOCK_TEXT_FTS" in detail for detail in details), (kind, details)
        assert any("UNION USING TEMP B-TREE" in detail for detail in details), (kind, details)
        assert not any(detail == "SCAN C" for detail in details), (kind, details)
        assert any(
            "MULTI-INDEX OR" in detail or (
                "SEARCH C " in detail and ("ROWID=?" in detail or "CARD_ID=?" in detail)
            ) for detail in details
        ), (kind, details)


@pytest.mark.parametrize("query,expected", [
    ("CLAUDE", {"card", "block"}), ("中文", {"card", "block"}),
    ("cl", {"card", "block"}), ("Claude中文", {"card", "block"}),
    ("50%", {"card", "block"}), ("a_b", {"card", "block"}),
    (r"a\b", {"card", "block"}), ('a"b', {"card", "block"}),
    ("%", {"card", "block"}), ("_", {"card", "block"}),
    ("Claude 中文", {"card", "block"}), ("Claude absent", set()),
    ("ÉCOLE", {"card", "block"}), ("Straße", set()),
    ("éc", {"card", "block"}), ("ÉC", set()),
])
def test_keyword_trigram_and_short_like(tmp_path, query, expected):
    import backend.infrastructure.storage.card_preview_query as module

    text = 'Claude中文 50% a_b a\\b a"b école STRASSE'
    for card_id, title in (("card", text), ("block", "正文在block中"), ("miss", "50x axb ab"),
                           ("gap", "内部块"), ("generic", "平台通用页")):
        _store_card(tmp_path, card_id=card_id, platform="xiaohongshu", title=title, author="作者")
    blocks = CardContentBlockRepository(tmp_path)
    for card_id, kind, provenance in (("block", "video_transcript", ""),
                                      ("gap", "enrichment_gap", ""),
                                      ("generic", "linked_article", "Generic article fetch via async enrichment")):
        blocks.upsert_block({"block_id": card_id, "card_id": card_id, "position": 0,
                             "layer": "derived", "block_kind": kind, "status": "preserved",
                             "body": text, "provenance": provenance})
    clause, _ = module._term_clause(query.split()[0])
    assert ("match ?" in clause) is (len(query.split()[0]) >= 3)
    page = read_card_preview_page(tmp_path, query=query)
    assert {i["card_id"] for i in page["items"]} == expected


@pytest.mark.parametrize("since,until", [
    ("2026-07-16T10:00:00Z", "2026-07-16T10:00:00.500000Z"),
    ("2026-07-16T10:00:00Z", None),
    (None, "2026-07-16T10:00:00Z"),
    ("2026-07-16T12:00:00.500000+02:00", "2026-07-16T10:00:00.500000Z"),
])
def test_published_bounds_equal_python_filter(tmp_path, since, until):
    from backend.api.mcp.server import _date

    times = {
        "before": "2026-07-16T09:59:59.999999Z",
        "start": "2026-07-16T10:00:00Z",
        "inside": "2026-07-16T10:00:00.000001Z",
        "end": "2026-07-16T12:00:00.500000+02:00",
        "after": "2026-07-16T10:00:00.500001Z",
        "unknown": None, "invalid": "not a timestamp",
        "unix": "1784196000", "rfc": "Thu, 16 Jul 2026 10:00:00 GMT",
    }
    for card_id in times:
        _store_card(tmp_path, card_id=card_id, platform="rss", title="search", author="作者")
    for card_id, published_at in times.items():
        _set_card_publication(tmp_path, card_id, published_at)
    start, end = _date(since), _date(until)
    old = read_card_preview_page(tmp_path, query="search")
    expected = [c for c in old["items"] if (published := _date(c["published_at"])) is not None
                and (start is None or published >= start) and (end is None or published <= end)]
    page = read_card_preview_page(tmp_path, query="search", published_since=start, published_until=end)
    assert page["items"] == expected
    assert page["total"] == len(expected)
    assert not {"unknown", "invalid"} & {c["card_id"] for c in page["items"]}
    if since == "2026-07-16T10:00:00Z" and until:
        assert {"start", "end", "inside"} <= {c["card_id"] for c in page["items"]}
    paged = read_card_preview_page(tmp_path, query="search", published_since=start,
                                   published_until=end, limit=1, offset=1)
    assert paged["total"] == len(expected) and paged["items"] == expected[1:2]


def test_published_bounds_require_timezone(tmp_path):
    with pytest.raises(ValueError, match="timezone"):
        read_card_preview_page(tmp_path, published_since=datetime(2026, 7, 16))
