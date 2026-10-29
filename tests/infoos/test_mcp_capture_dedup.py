"""Agent link identity deduplication; all DNS and HTTP are local doubles."""
from concurrent.futures import ThreadPoolExecutor
import socket
from types import SimpleNamespace

import pytest

from backend.application.pipeline.information_card_builder import build_information_card, information_card_payload
from backend.application.pipeline.normalize import normalize_raw_item
from backend.application.usecases.agent_capture import find_existing_capture
from backend.application.usecases.manual_capture import submit_manual_capture
from backend.domain.models.raw_item import RawItem
from backend.infrastructure import public_http
from backend.infrastructure.storage.capture_lookup import CARD_BY_ID, RAW_EXACT, RAW_RANGE, ACTIVE_BY_RAW, PENDING_CAPTURE
from backend.infrastructure.storage.database import connect
from backend.infrastructure.storage.paths import resolve_sqlite_path
from backend.infrastructure.storage.raw_items import RawItemRepository
from backend.infrastructure.storage.repositories import CardRepository
from tests.infoos.test_mcp_collect import setup, call  # noqa: F401


@pytest.fixture(autouse=True)
def public_network(monkeypatch):
    original = public_http.validate_and_pin_public_url
    def validate(url, **kwargs):
        kwargs["resolver"] = lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]
        return original(url, **kwargs)
    monkeypatch.setattr(public_http, "validate_and_pin_public_url", validate)
    monkeypatch.setattr(public_http, "select_supported_proxy", lambda _: None)
    monkeypatch.setattr("backend.application.usecases.capture_policy.validate_and_pin_public_url", validate)
    def no_http(*a, **kw):
        raise AssertionError("HTTP must be mocked explicitly")
    monkeypatch.setattr(public_http, "open_pinned_url", no_http)


def seed_card(base, platform, raw_id, url, *, status="active", scope="maintained"):
    source = f"regular-{platform}"
    with connect(resolve_sqlite_path(base)) as conn:
        conn.execute("""insert into sources(source_id,source_scope,platform,source_type,identifier,display_name,
            cadence_hours,connector_status,connector_id,created_at,updated_at)
            values(?,?,?,?,?,?,1,'ready',?,'2026-10-14T00:00:00Z','2026-10-14T00:00:00Z')""",
            (source, scope, platform, "creator", "author", "常规来源", source))
    raw = RawItem(connector_id=source, raw_item_id=raw_id, source_platform=platform,
                  source_type="social_post", source_url=url, raw_text="内容正文",
                  captured_at="2026-10-14T00:00:00Z",
                  raw_metadata={"source_id": source, "source_scope": scope, "title": "已有卡片"})
    RawItemRepository(base).upsert_raw_items([raw])
    card = build_information_card(normalize_raw_item(raw), raw)
    payload = information_card_payload(card)
    payload["status"] = status
    CardRepository(base).upsert_card(payload)
    return card.card_id


def counts(base):
    with connect(resolve_sqlite_path(base)) as conn:
        return tuple(conn.execute(f"select count(*) from {table}").fetchone()[0]
                     for table in ("capture_receipts", "tasks", "raw_items"))


def redirect_chain(monkeypatch, targets):
    calls = []
    def opener(url, **kwargs):
        assert kwargs["headers"] == {} and kwargs["timeout"] == 5
        assert kwargs["pinned_ip"] == "8.8.8.8"
        calls.append(url)
        target = targets[len(calls) - 1]
        if isinstance(target, Exception):
            raise target
        return SimpleNamespace(status_code=302, headers={"location": target, "set-cookie": "ignored=1"},
                               close=lambda: None)
    monkeypatch.setattr(public_http, "open_pinned_url", opener)
    return calls


@pytest.mark.parametrize("platform,raw_id,stored,submitted", [
    ("x", "123456", "https://x.com/alice/status/123456", "https://x.com/i/status/123456"),
    ("youtube", "https://www.youtube.com/watch?v=abc123XYZ00", "https://www.youtube.com/watch?v=abc123XYZ00", "https://youtu.be/abc123XYZ00?si=share"),
    ("youtube", "https://youtu.be/abc123XYZ00", "https://youtu.be/abc123XYZ00", "https://www.youtube.com/watch?v=abc123XYZ00"),
    ("xiaohongshu", "https://www.xiaohongshu.com/search_result/abcd1234?xsec_token=secret", "https://www.xiaohongshu.com/search_result/abcd1234?xsec_token=secret", "https://www.xiaohongshu.com/explore/abcd1234"),
    ("xiaohongshu", "abcd1234", "https://www.xiaohongshu.com/explore/abcd1234?xsec_token=secret", "https://www.xiaohongshu.com/discovery/item/abcd1234"),
    ("reddit", "t3_abc123", "https://www.reddit.com/r/test/comments/abc123/title/", "https://www.reddit.com/comments/abc123"),
    ("reddit", "abc123", "https://www.reddit.com/r/test/comments/abc123/title/", "https://www.reddit.com/comments/abc123"),
])
@pytest.mark.parametrize("scope", ["maintained", "research"])
def test_cross_source_identity_and_no_writes(setup, tmp_path, platform, raw_id, stored, submitted, scope):
    client, _, writer, _ = setup
    card_id = seed_card(tmp_path, platform, raw_id, stored, scope=scope)
    before = counts(tmp_path)
    quota = call(client, writer, "get_collect_capabilities")["quota"]
    result = call(client, writer, "capture_url", url=f"分享（{submitted}），第二条 https://8.8.8.8/other。")
    assert result["status"] == "already_collected" and result["card_id"] == card_id
    assert result["deep_link"]["url"] == f"/r/card/{card_id}"
    assert result["first_seen_at"] and result["evaluation"] is None
    assert result["resolution"] == "not_needed" and result["resolved_url"] == submitted
    assert counts(tmp_path) == before
    assert call(client, writer, "get_collect_capabilities")["quota"] == quota


def test_douyin_short_link_and_evaluation(setup, tmp_path, monkeypatch):
    client, _, writer, _ = setup
    url = "https://www.douyin.com/video/1234567890"
    card_id = seed_card(tmp_path, "douyin", url, url)
    with connect(resolve_sqlite_path(tmp_path)) as conn:
        conn.execute("insert into interest_profiles values(1,'profile','{}','hash','test','now')")
        conn.execute("""insert into card_evaluations(evaluation_id,card_id,card_version,profile_version,prompt_version,
            model,provider_id,quality,relevance,content_type,suggested_action,paused_landmark,summary,relation_note,passed_quality,created_at)
            values('eval',?,1,1,'v1','external','agent',90,80,'method','read',0,'摘要','关系',1,'now')""", (card_id,))
    calls = redirect_chain(monkeypatch, ["https://v.iesdouyin.com/next/", "https://www.iesdouyin.com/share/video/1234567890/?a=1"])
    before = counts(tmp_path)
    result = call(client, writer, "capture_url", url="看这个 https://v.douyin.com/one/！")
    assert result["status"] == "already_collected" and result["card_id"] == card_id
    assert result["evaluation"] == {"quality": 90, "relevance": 80, "passed_quality": True, "summary": "摘要"}
    assert result["resolution"] == "resolved" and len(calls) == 2
    assert counts(tmp_path) == before


@pytest.mark.parametrize("target", ["http://127.0.0.1/private", "https://example.com/post", OSError("timeout")])
def test_short_link_failure_falls_back(setup, tmp_path, monkeypatch, target):
    client, _, writer, _ = setup
    calls = redirect_chain(monkeypatch, [target])
    result = call(client, writer, "capture_url", url="https://v.douyin.com/fail/")
    assert result["status"] == "accepted" and result["resolution"] == "failed"
    assert result["resolved_url"] == "https://v.douyin.com/fail/" and result["resolution_reason"]
    assert len(calls) == 1 and counts(tmp_path)[:2] == (1, 1)


def test_xhs_short_link_and_redirect_limit(setup, tmp_path, monkeypatch):
    client, _, writer, _ = setup
    card_id = seed_card(tmp_path, "xiaohongshu", "note123", "https://www.xiaohongshu.com/explore/note123")
    redirect_chain(monkeypatch, ["https://www.xiaohongshu.com/discovery/item/note123?xsec_token=test"])
    result = call(client, writer, "capture_url", url="https://xhslink.com/one")
    assert result["card_id"] == card_id and result["resolution"] == "resolved"
    calls = redirect_chain(monkeypatch, ["https://xhslink.com/loop"] * 6)
    result = call(client, writer, "capture_url", url="https://xhslink.com/loop")
    assert result["resolution"] == "failed" and len(calls) == 6


def test_refresh_and_pending_identity(setup, tmp_path):
    client, _, writer, _ = setup
    card_id = seed_card(tmp_path, "x", "1234", "https://x.com/alice/status/1234", status="processing")
    before = counts(tmp_path)
    assert call(client, writer, "capture_url", url="https://x.com/i/status/1234")["card_id"] == card_id
    assert counts(tmp_path) == before
    first = call(client, writer, "capture_url", url="https://x.com/i/status/1234", refresh=True)
    assert first["status"] == "accepted"
    again = call(client, writer, "capture_url", url="https://twitter.com/alice/status/1234")
    assert again["status"] == "already_in_progress" and again["capture_id"] == first["capture_id"]
    with connect(resolve_sqlite_path(tmp_path)) as conn:
        conn.execute("update capture_receipts set processing_status='running'")
        conn.execute("update cards set status='active' where card_id=?", (card_id,))
    assert call(client, writer, "capture_url", url="https://x.com/i/status/1234")["status"] == "already_collected"
    assert call(client, writer, "capture_url", url="https://x.com/i/status/1234", refresh=True)["status"] == "accepted"
    assert call(client, writer, "get_collect_capabilities")["quota"]["capture"]["remaining"] == 198
    assert counts(tmp_path)[0] == 2


def test_no_link_invalid_and_share_punctuation(setup):
    client, _, writer, _ = setup
    assert call(client, writer, "capture_url", url="没有链接")["error"] == "invalid_capture_request"
    result = call(client, writer, "capture_url", url="链接 https://8.8.8.8/first). 第二个 https://8.8.8.8/second")
    assert result["resolved_url"] == "https://8.8.8.8/first"
    hosts = call(client, writer, "get_collect_capabilities")["capture"]["short_link_hosts"]
    assert {"v.douyin.com", "v.iesdouyin.com", "xhslink.com"} <= set(hosts)


def test_indexed_lookup_plans(setup, tmp_path):
    queries = [(CARD_BY_ID, ("card",)), (RAW_EXACT, ("source", "id")),
               (RAW_RANGE, ("source", "url?", "url?\uffff")),
               (ACTIVE_BY_RAW, ("source", "id")), (PENDING_CAPTURE, ("identity",))]
    with connect(resolve_sqlite_path(tmp_path)) as conn:
        for query, params in queries:
            plan = " ".join(row[3] for row in conn.execute("explain query plan " + query, params))
            assert "SEARCH" in plan and "SCAN" not in plan, plan


@pytest.mark.parametrize("status", ["active", "processing"])
def test_rss_url_capture_preserves_origin_and_quota(setup, tmp_path, status):
    client, _, writer, _ = setup
    url = "https://github.com/anthropics/knowledge-work-plugins"
    card_id = seed_card(tmp_path, "rss", url, url, status=status)
    with connect(resolve_sqlite_path(tmp_path)) as conn:
        before_card = dict(conn.execute("select * from cards where card_id=?", (card_id,)).fetchone())
    before_counts = counts(tmp_path)
    quota = call(client, writer, "get_collect_capabilities")["quota"]
    result = call(client, writer, "capture_url", url=url, refresh=False)
    assert result["status"] == ("already_collected" if status == "active" else "already_in_progress")
    assert result["card_id"] == card_id
    assert counts(tmp_path) == before_counts
    assert call(client, writer, "get_collect_capabilities")["quota"] == quota
    with connect(resolve_sqlite_path(tmp_path)) as conn:
        assert dict(conn.execute("select * from cards where card_id=?", (card_id,)).fetchone()) == before_card


def test_transaction_recheck_prevents_concurrent_duplicates(setup, tmp_path):
    _, _, writer, _ = setup
    def submit(index):
        return submit_manual_capture(tmp_path, token_id=writer["token"]["token_id"],
            payload={"url": "https://www.youtube.com/watch?v=abc123XYZ00", "capture_mode": "url_only",
                     "captured_at": "2026-10-14T00:00:00Z", "client_capture_id": f"parallel-{index}"},
            daily_limit=200, deduplicate=True).receipt
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    assert {r["status"] for r in results} == {"accepted", "already_in_progress"}
    assert counts(tmp_path)[:2] == (1, 1)
    assert find_existing_capture(tmp_path, "https://youtu.be/abc123XYZ00")["status"] == "already_in_progress"


def test_captured_xhs_tokens_match_without_requeue(setup, tmp_path):
    client, _, writer, _ = setup
    original = "https://www.xiaohongshu.com/explore/note123?xsec_token=one"
    first = call(client, writer, "capture_url", url=original)
    card_id = seed_card(tmp_path, "xiaohongshu", "unrelated", original)
    with connect(resolve_sqlite_path(tmp_path)) as conn:
        conn.execute("update capture_receipts set processing_status='succeeded',card_id=? where capture_id=?",
                     (card_id, first["capture_id"]))
    before = counts(tmp_path)
    result = call(client, writer, "capture_url", url="https://www.xiaohongshu.com/explore/note123?xsec_token=two")
    assert result["status"] == "already_collected" and result["card_id"] == card_id
    assert counts(tmp_path) == before


def test_agent_alias_identity_keeps_browser_observations_unchanged(setup, tmp_path):
    client, _, writer, _ = setup
    search = "https://www.xiaohongshu.com/search_result/note123?xsec_token=one"
    explore = "https://www.xiaohongshu.com/explore/note123?xsec_token=two"
    first = call(client, writer, "capture_url", url=search)
    pending = call(client, writer, "capture_url", url=explore)
    assert pending["capture_id"] == first["capture_id"] and pending["status"] == "already_in_progress"
    # The extension still creates observations; alias expansion is MCP-only.
    token_id = writer["token"]["token_id"]
    for index, url in enumerate((search, explore)):
        receipt = submit_manual_capture(tmp_path, token_id=token_id, payload={
            "url": url, "capture_mode": "url_only", "captured_at": "2026-10-14T00:00:00Z",
            "client_capture_id": f"browser-{index}",
        }).receipt
        assert receipt["status"] == "accepted"
    assert counts(tmp_path)[0] == 3
