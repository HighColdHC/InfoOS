"""规则候选 → agent 拆解 → 用户口述核实，评估与核实历史各自独立。"""
from backend.application.tasks.types import TaskType
from tests.infoos.e2e.support import (
    PROFILE, START, assert_denied, build_seeded, call, drain, evaluation, seed_raw,
)
from tests.infoos.test_opportunity_domain import item as breakdown
from tests.infoos.test_verification_domain import item as verification


def test_opportunity_journey(journey, tmp_path, monkeypatch):
    client, scheduled, interactive = journey
    call(client, scheduled, "push_profile", markdown=PROFILE)
    evidence = {"prefilter": {"status": "matched", "rules_version": "prefilter_v3",
                              "hits": [{"field": "desc", "token": "培训"}]}}
    seed_raw(tmp_path, monkeypatch, "douyin", platform="douyin", source_type="creator", metadata=evidence)
    seed_raw(tmp_path, monkeypatch, "wind", platform="zsxq", source_type="opportunity")
    seed_raw(tmp_path, monkeypatch, "ordinary", platform="douyin", source_type="creator",
             metadata={"prefilter": {"status": "missed", "rules_version": "prefilter_v3", "hits": []}})
    cards = build_seeded(tmp_path)
    page = call(client, scheduled, "list_opportunity_candidates", since=START, limit=1)
    assert page == call(client, scheduled, "list_opportunity_candidates", since=START, limit=1, cursor="")
    next_page = call(client, scheduled, "list_opportunity_candidates", since=START, limit=1, cursor=page["next_cursor"])
    candidates = page["items"] + next_page["items"]
    assert {r["card_id"] for r in candidates} == {cards["douyin"], cards["wind"]}
    assert next_page["next_cursor"] is None
    dy = next(r for r in candidates if r["card_id"] == cards["douyin"])
    assert dy["prefilter_summary"] == evidence["prefilter"]
    call(client, scheduled, "submit_evaluations", evaluator="journey", items=[evaluation(cards["douyin"])])
    drain(tmp_path, TaskType.CARD_FINALIZE)
    before = call(client, scheduled, "get_card", card_id=cards["douyin"])["evaluation"]
    results = call(client, scheduled, "submit_opportunity_breakdowns", evaluator="journey",
                   items=[breakdown(cards[k]) for k in ("douyin", "wind")])["results"]
    assert all(r["status"] == "accepted" for r in results)
    drain(tmp_path, TaskType.CARD_FINALIZE)
    opportunities = call(client, scheduled, "list_opportunities")["items"]
    assert {r["card_id"] for r in opportunities} == {cards["douyin"], cards["wind"]}
    assert call(client, scheduled, "list_opportunities", cursor="")["items"] == opportunities
    detail = call(client, scheduled, "get_card", card_id=cards["douyin"])
    assert detail["opportunity"]["offer"] == "培训课程"
    assert any(b["block_kind"] == "opportunity_breakdown" for b in detail["content_blocks"])
    assert detail["evaluation"] == before and before["prompt_version"] == "triage_v1"

    args = verification(card_id=cards["douyin"], price="2999 元")
    assert_denied(client, scheduled, "submit_verification", **args)
    first = call(client, interactive, "submit_verification", **args)
    drain(tmp_path, TaskType.CARD_FINALIZE)
    second = call(client, interactive, "submit_verification", **verification(
        card_id=cards["douyin"], supersedes=first["verification_id"], conclusion="drop", note="交付无法验证"))
    drain(tmp_path, TaskType.CARD_FINALIZE)
    history = call(client, scheduled, "list_verifications", card_id=cards["douyin"])["items"]
    assert [r["verification_id"] for r in history] == [second["verification_id"], first["verification_id"]]
    assert history[1]["superseded_by"] == second["verification_id"] and history[1]["price"] == "2999 元"
    assert call(client, scheduled, "list_verifications", card_id=cards["douyin"], cursor="")["items"] == history
    rejected = call(client, interactive, "submit_verification", **verification(card_id=cards["douyin"], note="微信:abc123"))
    assert rejected["error"] == "contact_info_not_allowed"
    assert call(client, scheduled, "list_verifications", card_id=cards["douyin"])["items"] == history
    detail = call(client, scheduled, "get_card", card_id=cards["douyin"])
    assert detail["verifications"] == history
    verified = [b for b in detail["content_blocks"] if b["block_kind"] == "opportunity_verification"]
    assert len(verified) == 2 and all(b["body"].startswith("人工核实") for b in verified)
    assert detail["evaluation"] == before
