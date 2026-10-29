"""ZSXQ bounded backfill, observation updates, and public digest projections."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.infoos._factories import card_count
from backend.app.main import create_app
from backend.application.tasks.handlers.acquisition import handle_card_build
from backend.application.tasks.input_versions import enrichment_input_version, card_finalize_input_version
from backend.application.tasks.planner import raw_item_target_id
from backend.application.tasks.types import Task, TaskType
from backend.application.usecases.api_tokens import create_token
from backend.domain.intelligence.rubric import RUBRIC_VERSION
from backend.domain.research import normalize_research_request
from backend.domain.sources.configuration import validate_source_payload
from backend.domain.sources.configuration_projection import database_source_to_connector_config
from backend.domain.sources.zsxq import validate_zsxq_search_options, zsxq_search_command_args
from backend.infrastructure.connectors.opencli import OpenCLIConnector
from backend.infrastructure.connectors.research import ResearchConnector, _opencli_research_config
from backend.infrastructure.storage.card_evaluations import CardEvaluationRepository
from backend.infrastructure.storage.cards import CardRepository
from backend.infrastructure.storage.interest_profiles import InterestProfileRepository
from backend.infrastructure.storage.raw_items import RawItemRepository
from backend.read_models.new_cards import list_new_cards
from tests.infoos.test_card_preview_api import _client, _store_card
from tests.infoos.test_mcp_interface import _call

DATE_RANGE = {"start_date": "2026-08-26", "end_date": "2026-09-01", "timezone": "Asia/Shanghai"}


def _request(content_type="all"):
    return {"name": "知识星球回补", "keywords": ["*"], "date_range": DATE_RANGE,
            "platforms": [{"platform": "zsxq", "max_items": 100, "sort": "earliest",
                           "options": {"content_type": content_type}}]}


@pytest.mark.parametrize("content_type", ["all", "essence"])
def test_wildcard_source_and_research_commands(content_type):
    metadata = {"zsxq_search": {"content_type": content_type, "sort": "earliest", **DATE_RANGE}}
    validate_zsxq_search_options(metadata, " * ")
    args = zsxq_search_command_args("*", metadata)
    assert args == ["--sort", "earliest", "--content-type", content_type,
                    "--start-date", "2026-08-26", "--end-date", "2026-09-01"]
    source = validate_source_payload({"source_id": "wildcard", "platform": "zsxq", "source_type": "keyword", "identifier": "*",
        "display_name": "全部帖", "cadence_hours": 24, "max_items_per_run": 100, "raw_metadata": metadata})
    assert database_source_to_connector_config(source)["command"] == [
        "zsxq", "search", "*", "--group_id", "1824528822", "--limit", "100", *args]
    normalized = normalize_research_request(_request(content_type))
    assert normalized["platforms"][0]["sort"] == "earliest"
    config = _opencli_research_config(platform="zsxq", keyword="*", limit=100, connector_id="research",
        options=normalized["platforms"][0]["options"], date_range=normalized["date_range"], sort="earliest")
    assert config["command"] == ["zsxq", "search", "*", "--group_id", "1824528822", "--limit", "100", *args]


@pytest.mark.parametrize("content_type", ["bid", "topic", "opportunity", "square"])
def test_wildcard_rejects_other_types(content_type):
    with pytest.raises(ValueError, match="all.*essence"):
        validate_zsxq_search_options({"zsxq_search": {"content_type": content_type, **DATE_RANGE}}, "*")
    with pytest.raises(ValueError):
        normalize_research_request(_request(content_type))


@pytest.mark.parametrize("content_type", ["all", "essence"])
def test_wildcard_requires_start_date(content_type):
    with pytest.raises(ValueError, match="start_date"):
        validate_zsxq_search_options({"zsxq_search": {"content_type": content_type}}, "*")
    request = _request(content_type)
    request.pop("date_range")
    with pytest.raises(ValueError, match="start_date"):
        normalize_research_request(request)


def test_old_research_command_unchanged():
    assert _opencli_research_config(platform="zsxq", keyword="AI", limit=20, connector_id="research")["command"] == [
        "zsxq", "search", "AI", "--group_id", "1824528822", "--limit", "20"]


def _raw(connector="all", digest=False):
    config = _opencli_research_config(platform="zsxq", keyword="AI", limit=20, connector_id=connector)
    if connector != "research":
        source = {"source_id": connector, "connector_id": connector, "platform": "zsxq",
                  "source_type": "topics" if connector == "all" else "keyword",
                  "identifier": "all" if connector == "all" else "AI", "raw_metadata": {}, "max_items_per_run": 20}
        projected = database_source_to_connector_config(source)
        config = {key: value for key, value in projected.items() if key in OpenCLIConnector.__dataclass_fields__}
    row = {"topic_id": "123456", "url": "https://wx.zsxq.com/topic/123456", "title": "回补正文",
           "full_text": "这是一篇包含具体方法与证据的原始帖子正文。", "time": "2026-08-27T12:00:00+08:00",
           "author": "作者", "tags": ["AI"], "readers": 10}
    if digest is not None:
        row["is_digest"] = digest
    return OpenCLIConnector(**config)._to_raw_item(row)


def _build(base, raw):
    RawItemRepository(base).upsert_raw_items([raw])
    return handle_card_build(Task(task_id=f"build-{raw.connector_id}", task_type=TaskType.CARD_BUILD,
        target_kind="raw_item", target_id=raw_item_target_id(raw.connector_id, raw.raw_item_id)), base)["artifacts"]


def _evaluate(base, card_id):
    with CardRepository(base).connect() as conn:
        InterestProfileRepository.insert_current(conn, markdown="profile", sections={}, content_hash="profile", source="test")
        CardEvaluationRepository.insert(conn, {
            "evaluation_id": "eval", "card_id": card_id, "card_version": 1, "profile_version": 1,
            "prompt_version": RUBRIC_VERSION, "model": "agent", "provider_id": "external",
            "quality": 90, "relevance": 90, "attribution_kind": "project", "attribution_ref": "InfoOS",
            "content_type": "method", "suggested_action": "read", "paused_landmark": False,
            "summary": "摘要", "relation_note": "相关", "passed_quality": True, "receipt_id": None})


@pytest.mark.parametrize("initial", [False, None])
@pytest.mark.parametrize("connector", ["all", "keyword", "research"])
def test_digest_upgrade_preserves_evaluation_and_entry(tmp_path, initial, connector):
    raw = _raw(digest=initial)
    card_id = _build(tmp_path, raw)["card_id"]
    cards = CardRepository(tmp_path)
    cards.finalize_card(card_id, content_hash="sha256:" + "a" * 64, completeness_status="complete", missing_reasons=[])
    _evaluate(tmp_path, card_id)
    before = cards.get_card(card_id)
    before_eval = CardEvaluationRepository(tmp_path).latest_for_card(card_id)
    original_raw = RawItemRepository(tmp_path).get_for_connector("all", raw.raw_item_id)
    assert list_new_cards(tmp_path, "2026-01-01T00:00:00Z")["items"] == []
    upgraded = _raw(connector, True)
    if connector == "research":
        upgraded = upgraded.model_copy(update={"raw_metadata": {**upgraded.raw_metadata,
            "source_scope": "research", "matched_keywords": ["*"], "research_batch_id": "batch"}})
    changed = RawItemRepository(tmp_path).upsert_raw_items_with_changes([upgraded])
    if connector == "all":
        assert changed == []
    else:
        assert _build(tmp_path, upgraded)["card_id"] == card_id
    assert card_count(tmp_path) == 1
    after = cards.get_card(card_id)
    assert after["raw_payload"]["raw_payload"]["is_digest"] is True
    for key in ("captured_at", "first_seen_at", "status", "version", "content_hash", "raw_connector_id", "raw_item_id"):
        assert after[key] == before[key]
    assert enrichment_input_version(after) == enrichment_input_version(before)
    assert card_finalize_input_version(after, [], []) == card_finalize_input_version(before, [], [])
    assert CardEvaluationRepository(tmp_path).latest_for_card(card_id) == before_eval
    assert list_new_cards(tmp_path, "2026-01-01T00:00:00Z")["items"] == []
    assert RawItemRepository(tmp_path).get_for_connector("all", raw.raw_item_id)["first_seen_at"] == original_raw["first_seen_at"]
    _build(tmp_path, _raw(connector, False))
    assert cards.get_card(card_id)["raw_payload"]["raw_payload"]["is_digest"] is True


def test_content_changes_still_rebuild(tmp_path):
    raw = _raw()
    card_id = _build(tmp_path, raw)["card_id"]
    changed = raw.model_copy(update={"raw_text": "真正修改后的正文内容"})
    _build(tmp_path, changed)
    assert CardRepository(tmp_path).get_card(card_id)["content"] == "真正修改后的正文内容"


def test_preview_digest_filter_and_null(tmp_path):
    client = _client(tmp_path)
    for card_id, platform, metadata in [("true", "zsxq", {"is_digest": True}), ("false", "zsxq", {"is_digest": False}),
        ("old", "zsxq", {}), ("other", "rss", {"is_digest": True})]:
        _store_card(tmp_path, card_id=card_id, platform=platform, title="正文", author="作者", raw_payload={"raw_payload": metadata})
    result = client.get("/api/card-preview").json()
    assert {item["card_id"]: item["is_digest"] for item in result["items"]} == {"true": True, "false": False, "old": None, "other": None}
    for flag, expected in [(1, "true"), (0, "false")]:
        page = client.get("/api/card-preview", params={"digest": flag, "limit": 1}).json()
        assert page["total"] == 1 and page["items"][0]["card_id"] == expected
        assert client.get("/api/card-preview", params={"digest": flag, "offset": 1}).json()["items"] == []
        assert client.get(f"/api/card-preview/{expected}").json()["card"]["is_digest"] is bool(flag)
    for invalid in ["", "true", "2", "-1", " 1"]:
        response = client.get("/api/card-preview", params={"digest": invalid})
        assert response.status_code == 422 and response.json()["code"] == "invalid_digest"


@pytest.mark.parametrize("digest", [True, False, None])
def test_adapter_row_to_preview_and_all_mcp_tools(tmp_path, monkeypatch, digest):
    monkeypatch.delenv("INFOOS_PUBLIC_BASE_URL", raising=False)
    app = create_app(tmp_path)
    raw = _raw(digest=digest)
    card_id = _build(tmp_path, raw)["card_id"]
    CardRepository(tmp_path).finalize_card(card_id, content_hash="sha256:" + "a" * 64, completeness_status="complete", missing_reasons=[])
    _evaluate(tmp_path, card_id)
    token = create_token(tmp_path, name="reader", scopes=["intelligence:read"])["plaintext_token"]
    with TestClient(app) as client:
        assert client.get("/api/card-preview").json()["items"][0]["is_digest"] is digest
        assert client.get(f"/api/card-preview/{card_id}").json()["card"]["is_digest"] is digest
        for name, args in [("get_card", {"card_id": card_id}), ("get_cards", {"card_ids": [card_id]}),
            ("search", {"query": "回补"}), ("list_new_cards", {"since": "2026-01-01T00:00:00Z", "only_unevaluated": False}),
            ("get_updates_since", {"since": "2026-01-01T00:00:00Z"})]:
            response = _call(client, "tools/call", {"name": name, "arguments": args}, token)
            content = response.json()["result"]["structuredContent"]
            item = content if name == "get_card" else content["related"][0] if name == "get_updates_since" else content["items"][0]
            assert item["is_digest"] is digest


@pytest.mark.parametrize("content_type", ["all", "essence"])
def test_research_http_preserves_earliest_to_connector(tmp_path, content_type):
    from tests.infoos.research.test_research_api import _client as research_client
    from backend.infrastructure.storage.sources import SourceRepository
    from backend.application.workflows.fetch_support import build_connector
    client = research_client(tmp_path)
    response = client.post("/api/research-runs", json=_request(content_type), headers={"Idempotency-Key": "backfill"})
    assert response.status_code == 202
    run_id = response.json()["research_run"]["research_run_id"]
    source = SourceRepository(tmp_path).get_source(f"{run_id}_zsxq")
    connector = build_connector(tmp_path, database_source_to_connector_config(source))
    configs = []
    class Fake:
        def fetch_new_items(self, since_cursor):
            from backend.infrastructure.connectors.base import FetchResult
            return FetchResult(items=[_raw()], next_cursor=None)
    connector._opencli_connector = lambda config: configs.append(config) or Fake()
    result = connector.fetch_new_items(None)
    assert result.items[0].raw_metadata["is_digest"] is False
    assert configs[0]["command"] == ["zsxq", "search", "*", "--group_id", "1824528822", "--limit", "100",
        "--sort", "earliest", "--content-type", content_type, "--start-date", "2026-08-26", "--end-date", "2026-09-01"]
    assert result.items[0].raw_metadata["research_batch_id"] == run_id


def test_existing_legacy_research_card_is_reused_by_url(tmp_path):
    import hashlib
    raw = _raw("research").model_copy(update={"raw_metadata": {**_raw("research").raw_metadata, "source_scope": "research"}})
    from backend.application.pipeline.information_card_builder import build_information_card, information_card_payload
    from backend.application.pipeline.normalize import normalize_raw_item
    RawItemRepository(tmp_path).upsert_raw_items([raw])
    draft = build_information_card(normalize_raw_item(raw), raw)
    payload = information_card_payload(draft)
    payload["card_id"] = "card_" + hashlib.sha1(b"id:zsxq:123456").hexdigest()[:12]
    CardRepository(tmp_path).upsert_card(payload)
    assert _build(tmp_path, _raw("all", True))["card_id"] == payload["card_id"]
    assert card_count(tmp_path) == 1


def test_visible_digest_observation_survives_source_cursor(tmp_path, monkeypatch):
    raw = _raw()
    card_id = _build(tmp_path, raw)["card_id"]
    config = _opencli_research_config(platform="zsxq", keyword="AI", limit=20, connector_id="all")
    connector = OpenCLIConnector(**config)
    row = {**raw.raw_metadata["opencli_record"], "is_digest": True}
    monkeypatch.setattr(connector, "_load_payload", lambda: [row])
    result = connector._fetch_new_items_in_lease(since_cursor=raw.raw_item_id)
    assert len(result.items) == 1 and result.items[0].raw_metadata["is_digest"] is True
    assert result.next_cursor == raw.raw_item_id
    RawItemRepository(tmp_path).upsert_raw_items(result.items)
    assert CardRepository(tmp_path).get_card(card_id)["raw_payload"]["raw_payload"]["is_digest"] is True


@pytest.mark.parametrize("legacy", [False, True])
def test_reuse_zsxq_card_detects_body_only_changes(tmp_path, legacy):
    from backend.application.pipeline.information_card_builder import build_information_card, information_card_payload
    from backend.application.pipeline.normalize import normalize_raw_item
    from backend.application.pipeline.zsxq_observations import reuse_zsxq_card

    raw = _raw()
    draft = build_information_card(normalize_raw_item(raw), raw)
    payload = information_card_payload(draft)
    if legacy:
        payload["raw_payload"] = draft.model_dump(mode="json")
    CardRepository(tmp_path).upsert_card(payload)
    # No raw_items row: comparison must depend on stored card columns/hash.
    assert reuse_zsxq_card(tmp_path, raw, information_card_payload(draft))[1] is True
    changed = raw.model_copy(update={"raw_text": raw.raw_text + "新增正文。"})
    changed_draft = build_information_card(normalize_raw_item(changed), changed)
    incoming = information_card_payload(changed_draft)
    existing, unchanged = reuse_zsxq_card(tmp_path, changed, incoming)
    assert existing["card_id"] == payload["card_id"]
    assert unchanged is False
    CardRepository(tmp_path).upsert_card(incoming)
    assert CardRepository(tmp_path).get_card(payload["card_id"])["content"].endswith("新增正文。")
