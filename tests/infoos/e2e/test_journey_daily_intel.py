"""定时 agent 从画像、新卡评估到冻结日报与阅读面的完整路径。"""
from backend.application.tasks.daily_brief_planner import enqueue_daily_brief_build_task
from backend.application.tasks.types import TaskType
from tests.infoos.e2e.support import (
    PROFILE, START, build_seeded, call, drain, evaluation, invoke, reader_get, seed_raw,
)


def test_daily_intelligence_journey(journey, tmp_path, monkeypatch):
    client, token, _ = journey
    invalid = invoke(client, token, "push_profile", markdown="## 主线与KR\n- KR")
    assert invalid["isError"] and "暂放方向" in invalid["content"][0]["text"]
    assert call(client, token, "push_profile", markdown=PROFILE)["profile_version"] == 1
    assert call(client, token, "push_profile", markdown=PROFILE + "\n更新")["profile_version"] == 2
    assert call(client, token, "get_profile")["profile_version"] == 2
    assert call(client, token, "get_evaluation_rubric")["rubric_version"] == "triage_v1"

    keys = [f"related-{i:02}" for i in range(12)] + [f"explore-{i}" for i in range(4)]
    keys += ["low", "paused", "landmark"]
    for key in keys:
        seed_raw(tmp_path, monkeypatch, key)
    seed_raw(tmp_path, monkeypatch, "tomorrow", entered="2026-10-18T12:00:00Z")
    cards = build_seeded(tmp_path)
    args = {"since": START, "until": "2026-10-19T00:00:00Z", "only_unevaluated": True, "limit": 6}
    page = call(client, token, "list_new_cards", **args)
    assert call(client, token, "list_new_cards", **args, cursor="") == page
    seen = []
    while True:
        ids = [c["card_id"] for c in page["items"]]
        details = call(client, token, "get_cards", card_ids=ids)["items"]
        assert [c["card_id"] for c in details] == ids
        assert all("正文" in c["content"] and c["evaluation"] is None for c in details)
        seen.extend(ids)
        if not page["next_cursor"]:
            break
        page = call(client, token, "list_new_cards", **args, cursor=page["next_cursor"])
    assert len(seen) == len(set(seen)) == 20
    assert set(seen) == set(cards.values())

    items = [evaluation(cards[key], relevance=100-i) for i, key in enumerate(keys[:12])]
    items[11]["attribution_ref"] = "画像中不存在"
    items += [evaluation(cards[f"explore-{i}"], attribution_ref="画像中不存在", relevance=10, quality=90-i)
              for i in range(4)]
    items += [evaluation(cards["low"], quality=59),
              evaluation(cards["paused"], attribution_ref="游戏 [[暂停决策]]"),
              evaluation(cards["landmark"], attribution_ref="游戏 [[暂停决策]]", paused_landmark=True)]
    monkeypatch.setattr("backend.application.usecases.submit_evaluations.utc_now_micro_z",
                        lambda: "2026-10-17T22:00:00.000000Z")
    results = call(client, token, "submit_evaluations", evaluator="journey:scheduled", items=items)["results"]
    assert all(r["status"] == "accepted" for r in results)
    assert results[16]["passed_quality"] is False
    assert results[11]["normalized"]["attribution_kind"] == "domain"
    assert results[11]["normalized"]["attribution_ref"] is None
    assert results[12]["normalized"]["attribution_kind"] == "exploration"
    assert "attribution_ref_not_in_profile" in results[12]["warnings"]
    assert results[17]["normalized"]["attribution_kind"] == "paused"
    assert results[17]["normalized"]["relevance"] is None
    drain(tmp_path, TaskType.CARD_FINALIZE)
    passed = call(client, token, "get_card", card_id=cards["related-00"])
    blocks = {b["block_kind"]: b["body"] for b in passed["content_blocks"]}
    assert blocks["intel_summary"] == "中文摘要" and blocks["relation_note"] == "用于InfoOS项目。"
    low = call(client, token, "get_card", card_id=cards["low"])
    assert low["evaluation"]["passed_quality"] == 0
    assert not {"intel_summary", "relation_note"} & {b["block_kind"] for b in low["content_blocks"]}
    assert cards["low"] in {c["card_id"] for c in call(client, token, "search", query="low")["items"]}
    assert [c["card_id"] for c in call(client, token, "list_new_cards", **args)["items"]] == [cards["tomorrow"]]

    monkeypatch.setenv("INFOOS_PUBLIC_BASE_URL", "https://reader.example.test")
    monkeypatch.setattr("backend.infrastructure.storage.daily_briefs.utc_now", lambda: "2026-10-17T23:00:01Z")
    assert enqueue_daily_brief_build_task(tmp_path, now="2026-10-17T23:00:00Z") == "daily_brief_build_2026-10-18"
    drain(tmp_path, TaskType.DAILY_BRIEF_BUILD)
    brief = call(client, token, "get_daily_brief", date="2026-10-18")["brief"]
    assert brief["layout"] == "knowledge_v1"
    assert brief["items"] == []  # Only triaged so far; extraction is covered in the knowledge journey.
    assert brief["weekly_paused"] is None
    assert brief["deep_link"] == {"url": "https://reader.example.test/r/brief/2026-10-18", "relative_url": False}
    monkeypatch.setattr("backend.application.usecases.submit_evaluations.utc_now_micro_z",
                        lambda: "2026-10-18T13:00:00.000000Z")
    call(client, token, "submit_evaluations", evaluator="journey:scheduled", items=[evaluation(cards["tomorrow"])])
    drain(tmp_path, TaskType.CARD_FINALIZE)
    updates = call(client, token, "get_updates_since", since=brief["generated_at"])
    assert [r["card_id"] for r in updates["related"]] == [cards["tomorrow"]]
    assert call(client, token, "get_daily_brief", date="2026-10-18")["brief"] == brief
    reader_get(client, monkeypatch, f"/r/card/{cards['related-00']}")
    reader_get(client, monkeypatch, "/r/brief/2026-10-18")
    preview = reader_get(client, monkeypatch, f"/api/card-preview/{cards['related-00']}").json()
    assert preview["card"]["card_id"] == cards["related-00"]
