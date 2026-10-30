"""Browse filters and bounded batch projections, using local SQLite only."""

import pytest

from backend.infrastructure.storage import card_browse_query
from backend.infrastructure.storage.opportunity_breakdowns import OpportunityBreakdownRepository
from backend.infrastructure.storage.repositories import CardRepository, SourceRepository
from tests.infoos._factories import make_source_payload
from tests.infoos.test_card_preview_api import _client, _store_card, evaluated_preview as evaluated_preview
from tests.infoos.test_opportunity_domain import item


def ids(response):
    assert response.status_code == 200, response.text
    return [row["card_id"] for row in response.json()["items"]]


def test_browse_document_supports_direct_open_refresh_and_head(tmp_path):
    from backend.app.main import create_app
    from fastapi.testclient import TestClient

    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    markup = '<html><div id="root">InfoOS reader</div></html>'
    (dist / "index.html").write_text(markup)
    with TestClient(create_app(tmp_path)) as client:
        for path in ("/r/browse", "/r/browse?platform=x&evaluation=passed"):
            response = client.get(path)
            assert response.status_code == 200 and response.text == markup, (path, response.text)
            assert response.headers["content-type"].startswith("text/html")
        response = client.head("/r/browse")
        assert response.status_code == 200 and response.content == b""


def test_views_source_query_pagination_and_old_defaults(evaluated_preview):
    client = evaluated_preview
    path = "/api/card-preview"
    default = client.get(path).json()
    assert default["total"] == 7
    assert client.get(path + "?evaluation=").json() == default
    assert client.get(path + "?evaluation=passed").json() == client.get(path + "?recommended=1").json()
    assert ids(client.get(path + "?evaluation=unevaluated")) == ["unevaluated"]
    evaluated = ids(client.get(path + "?evaluation=evaluated"))
    assert len(evaluated) == 6 and evaluated[-1] == "rejected"
    assert evaluated[0] == "recommended-newer"
    assert ids(client.get(path + "?source_id=source-youtube")) == ["recommended-null"]
    assert ids(client.get(path + "?source_id=source-youtube&platform=x")) == []
    assert ids(client.get(path, params={"source_id": "' OR 1=1 --"})) == []
    filtered = client.get(path, params={"source_id": "source-x", "evaluation": "passed",
                                       "query": "关注 产品", "completeness": "partial", "limit": 1, "offset": 1}).json()
    assert filtered["total"] == 3 and len(filtered["items"]) == 1 and filtered["offset"] == 1
    # Newer failed evaluation must hide an older passed evaluation, while tied
    # timestamps follow evaluation_id DESC as the existing catalog does.
    passed = ids(client.get(path + "?evaluation=passed"))
    assert "rejected" not in passed and "recommended-high" in passed
    assert client.get(path + "?recommended=1&status=processing").json()["total"] == 0
    assert client.get(path + "?evaluation=passed&status=processing").json()["total"] == 0


def test_relevance_sort_and_view_validation(evaluated_preview):
    client = evaluated_preview
    path = "/api/card-preview"
    assert client.get(path + "?evaluation=passed&sort=relevance_desc").json() == client.get(
        path + "?recommended=1&sort=relevance_desc").json()
    for view in ("evaluated", "unevaluated", ""):
        assert client.get(path, params={"evaluation": view, "sort": "relevance_desc"}).json()["code"] == "invalid_sort"
    for params in ({"evaluation": "other"}, {"evaluation": "unevaluated", "recommended": "1"},
                   {"evaluation": "evaluated", "recommended": "1"}):
        response = client.get(path, params=params)
        assert response.status_code == 422 and response.json()["code"] == "invalid_evaluation"


@pytest.mark.parametrize("view", [{"evaluation": "passed"}, {"recommended": "1"}])
def test_relevance_page_uses_latest_scores_then_publication_and_id(evaluated_preview, tmp_path, view):
    from tests.infoos.test_card_preview_api import _store_evaluation

    for card_id in ("tie-a", "tie-z"):
        _store_card(tmp_path, card_id=card_id, platform="x", title="关注产品 " + card_id, author="作者")
        _store_evaluation(tmp_path, card_id, quality=90, relevance=90, passed=True)
    # The older evaluation still passes and has a higher score, but must never
    # supply the sort key or cause a duplicate row on any page.
    _store_evaluation(tmp_path, "recommended-high", quality=80, relevance=10, passed=True,
                      evaluation_id="a-high-latest", created_at="2026-07-18T00:00:00Z")
    params = {**view, "sort": "relevance_desc", "limit": 2}
    expected = ["recommended-quality", "recommended-newer", "tie-z", "tie-a",
                "recommended-low", "recommended-high", "recommended-null"]
    actual = []
    for offset in range(0, len(expected), 2):
        response = evaluated_preview.get("/api/card-preview", params={**params, "offset": offset})
        assert response.json()["total"] == len(expected)
        actual.extend(ids(response))
    assert actual == expected
    filtered = {**params, "source_id": "source-x", "platform": "x", "query": "关注 产品",
                "published_since": "2026-07-17T00:00:00Z", "published_until": "2026-07-18T00:00:00Z"}
    assert ids(evaluated_preview.get("/api/card-preview", params=filtered)) == ["recommended-newer"]


def test_facets_counts_exclusions_bounded_order_and_index(tmp_path, monkeypatch):
    client = _client(tmp_path)
    assert client.get("/api/card-preview/facets?scope=maintained").json()["sources"] == []
    for card_id, platform in (("x-1", "x"), ("x-2", "x"), ("rss", "rss"),
                              ("research", "youtube"), ("capture", "github"),
                              ("processing", "douyin"), ("withdrawn", "reddit")):
        _store_card(tmp_path, card_id=card_id, platform=platform, title=card_id, author="作者")
    SourceRepository(tmp_path).upsert_source(make_source_payload(source_id="empty", platform="github"))
    with CardRepository(tmp_path).connect() as conn:
        conn.execute("update sources set source_scope='research' where source_id='source-youtube'")
        conn.execute("update sources set raw_metadata=? where source_id='source-github'", ('{"system_managed":true}',))
        # Disabled maintained sources still have readable cards.
        conn.execute("update sources set enabled=0 where source_id='source-rss'")
        conn.execute("update cards set status='processing' where card_id='processing'")
        conn.execute("update cards set status='withdrawn' where card_id='withdrawn'")
        sql, params = card_browse_query._facet_query(scope="maintained")
        plan = " ".join(str(tuple(r)) for r in conn.execute("explain query plan " + sql, params))
        assert any(name in plan for name in (
            "idx_cards_active_updated", "idx_cards_status_sort", "idx_cards_status_published",
        )), plan
        assert "sqlite_autoindex_sources" in plan
    all_sources = client.get("/api/card-preview/facets").json()["sources"]
    assert {r["source_id"] for r in all_sources} == {"source-x", "source-rss", "source-youtube", "source-github"}
    result = client.get("/api/card-preview/facets?scope=maintained")
    assert result.status_code == 200
    facets = result.json()
    assert [(r["platform"], r["card_count"]) for r in facets["platforms"]] == [("x", 2), ("rss", 1)]
    assert facets["sources"] == [
        {"source_id": "source-x", "name": "X 观察源", "platform": "x", "card_count": 2, "evaluated_count": 0, "passed_count": 0},
        {"source_id": "source-rss", "name": "RSS 观察源", "platform": "rss", "card_count": 1, "evaluated_count": 0, "passed_count": 0},
    ]
    assert facets["limits"] == {"platforms": 100, "sources": 1000}
    assert facets["truncated"] == {"platforms": False, "sources": False}
    monkeypatch.setattr(card_browse_query, "SOURCE_LIMIT", 1)
    monkeypatch.setattr(card_browse_query, "PLATFORM_LIMIT", 1)
    bounded = client.get("/api/card-preview/facets?scope=maintained").json()
    assert len(bounded["sources"]) == len(bounded["platforms"]) == 1
    assert bounded["sources"][0] == facets["sources"][0]
    assert bounded["truncated"] == {"platforms": True, "sources": True}


def test_page_batches_evaluations_and_latest_opportunities_once(evaluated_preview, tmp_path):
    with CardRepository(tmp_path).connect() as conn:
        for key, version, stamp, step in (("old", 1, "2026-07-16T00:00:00Z", "skip"),
                                          ("new", 2, "2026-07-17T00:00:00Z", "observe")):
            OpportunityBreakdownRepository.upsert(conn, {
                **item("recommended-high", next_step=step), "breakdown_id": key,
                "raw_item_revision": None, "card_version": version, "lens_version": "opportunity_v1", "rules_version": "v3",
                "evaluator": "test", "token_id": "writer", "created_at": stamp,
            })
    page = evaluated_preview.get("/api/card-preview?evaluation=evaluated").json()
    rows = {r["card_id"]: r for r in page["items"]}
    assert rows["recommended-high"]["opportunity"] == {"has_breakdown": True, "next_step": "observe"}
    assert rows["rejected"]["opportunity"] is None
    assert {"quality", "relevance", "passed_quality", "summary", "relation_note", "attribution_kind"} <= rows["rejected"]["evaluation"].keys()


def test_facets_interval_latest_evaluations_and_unassociated_sources(evaluated_preview, tmp_path, monkeypatch):
    from tests.infoos.test_card_preview_api import _set_card_publication

    client = evaluated_preview
    # Legacy data can contain NULL, empty IDs or an ID with no source row.
    with CardRepository(tmp_path).connect() as conn:
        conn.execute("pragma foreign_keys=off")
        conn.execute("update cards set source_id=null where card_id='recommended-high'")
        conn.execute("update cards set source_id='' where card_id='rejected'")
        conn.execute("update cards set source_id='removed-source' where card_id='recommended-null'")
    _set_card_publication(tmp_path, "unevaluated", None)
    monkeypatch.setattr(card_browse_query, "SOURCE_LIMIT", 1)
    params = {"published_since": "2026-07-16", "published_until": "2026-07-17"}
    response = client.get("/api/card-preview/facets", params=params)
    assert response.status_code == 200
    facets = response.json()
    assert facets["limits"] == {"platforms": None, "sources": None}
    assert facets["truncated"] == {"platforms": False, "sources": False}
    assert len(facets["sources"]) == 3  # no SOURCE_LIMIT truncation in management statistics
    assert sum(r["card_count"] for r in facets["sources"]) == 5
    groups = {(r["source_id"], r["platform"]): r for r in facets["sources"]}
    assert groups[("__none__", "x")] == {"source_id": "__none__", "platform": "x", "name": "未关联来源",
        "card_count": 2, "evaluated_count": 2, "passed_count": 1}
    assert groups[("__none__", "youtube")]["card_count"] == 1
    assert [r["card_count"] for r in facets["sources"]] == [2, 2, 1]
    for source in facets["sources"]:
        filters = {**params, "source_id": source["source_id"], "platform": source["platform"]}
        for evaluation, count in (("", "card_count"), ("evaluated", "evaluated_count"), ("passed", "passed_count")):
            assert client.get("/api/card-preview", params={**filters, "evaluation": evaluation}).json()["total"] == source[count]
    assert client.get("/api/card-preview", params={**params, "source_id": "__none__"}).json()["total"] == 3
    assert client.get("/api/card-preview/facets", params={"published_until": "2020-01-01"}).json()["sources"] == []
    from datetime import datetime, timezone
    sql, values = card_browse_query._facet_query(published_until=datetime(2026, 7, 17, tzinfo=timezone.utc))
    with CardRepository(tmp_path).connect_existing() as conn:
        plan = " ".join(str(tuple(r)) for r in conn.execute("explain query plan " + sql, values))
    assert "idx_cards_status_published" in plan
    assert "idx_card_evaluations_card_latest" in plan
