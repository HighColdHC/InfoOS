"""真实采集流水线 → 分诊 → 拆解/归并 → 日报 → 知识卡与原卡阅读。"""
from backend.application.usecases.daily_brief import build_daily_brief
from tests.infoos.e2e.support import PROFILE, START, build_seeded, call, drain, evaluation, reader_get, seed_raw
from tests.infoos.test_knowledge_domain import knowledge


def test_knowledge_journey(journey, tmp_path, monkeypatch):
    client, scheduled, interactive = journey
    call(client, scheduled, "push_profile", markdown=PROFILE)
    seed_raw(tmp_path, monkeypatch, "first")
    seed_raw(tmp_path, monkeypatch, "second")
    ids = build_seeded(tmp_path)
    for module in ("backend.application.usecases.submit_knowledge_extractions", "backend.application.usecases.submit_evaluations",
                   "backend.infrastructure.storage.knowledge_queries"):
        monkeypatch.setattr(module + ".utc_now_micro_z", lambda: "2026-10-17T22:00:00.000000Z")
    assert len(call(client, scheduled, "get_knowledge_schema")["kinds"]) == 5
    call(client, scheduled, "submit_evaluations", evaluator="journey", items=[
        evaluation(card_id, extract_kinds=["opportunity"]) for card_id in ids.values()])
    drain(tmp_path)
    candidates = call(client, scheduled, "list_extraction_candidates", since=START)
    assert len(candidates["items"]) == 2
    def submit(card_id, value):
        return call(client, scheduled, "submit_knowledge_extractions", evaluator="journey", items=[{
            "card_id": card_id, "profile_version": 1, "outcome": "extracted", "cards": [value]}])["results"][0]
    first = submit(ids["first"], knowledge("opportunity", quotes=["培训课程的产品与成交证据。"], title="培训课程"))
    assert first["status"] == "accepted", first
    target = first["cards"][0]["knowledge_id"]
    matches = call(client, scheduled, "search_knowledge_cards", kind="opportunity", query="培训课程")
    assert matches["items"][0]["knowledge_id"] == target
    assert submit(ids["second"], {"kind": "opportunity", "action": "merge", "target_knowledge_id": target,
                                 "quotes": ["培训课程的产品与成交证据。"], "note": "同一门生意"})["status"] == "accepted"
    assert call(client, scheduled, "list_extraction_candidates", since=START)["items"] == []
    detail = call(client, scheduled, "get_knowledge_card", knowledge_id=target)
    assert len(detail["evidence"]) == 2 and detail["research_status"] == "pending"
    assert all(e["deep_link"].startswith("/r/card/") for e in detail["evidence"])
    assert call(client, interactive, "set_research_status", knowledge_id=target, status="researching")["changed"]
    brief = build_daily_brief(tmp_path, "2026-10-18")["brief"]
    assert brief["counts"]["opportunity"] == 1
    frozen = call(client, scheduled, "get_daily_brief", date="2026-10-18")["brief"]
    assert frozen["layout"] == "knowledge_v1" and frozen["items"][0]["knowledge_id"] == target
    assert frozen["items"][0]["deep_link"]["url"] == f"/r/knowledge/{target}"
    assert call(client, scheduled, "get_card", card_id=ids["first"])["knowledge_cards"][0]["knowledge_id"] == target
    assert call(client, scheduled, "get_cards", card_ids=[ids["second"]])["items"][0]["knowledge_cards"][0]["knowledge_id"] == target
    reader_get(client, monkeypatch, f"/r/knowledge/{target}")
    assert reader_get(client, monkeypatch, f"/api/intelligence/knowledge/{target}").json()["knowledge_id"] == target
    assert reader_get(client, monkeypatch, f"/api/card-preview/{ids['first']}").json()["knowledge_cards"][0]["knowledge_id"] == target


def test_knowledge_schema_uses_only_configured_archive_targets(journey, tmp_path, monkeypatch):
    client, scheduled, _ = journey
    config = tmp_path / "knowledge-config.yaml"
    monkeypatch.setenv("INFOOS_CONFIG_PATH", str(config))
    defaults = call(client, scheduled, "get_knowledge_schema")
    assert all(kind["pkm_target"] == "" for kind in defaults["kinds"])
    config.write_text('knowledge:\n  pkm_targets:\n    project: "案例库/"\n    topic: "内容项目/选题/"\n', encoding="utf-8")
    configured = call(client, scheduled, "get_knowledge_schema")
    targets = {kind["key"]: kind["pkm_target"] for kind in configured["kinds"]}
    assert targets == {"project": "案例库/", "opportunity": "", "method": "", "topic": "内容项目/选题/", "news": ""}
    assert configured["profile_version"] == defaults["profile_version"]
    assert all(kind["archive_purpose"] for kind in configured["kinds"])
