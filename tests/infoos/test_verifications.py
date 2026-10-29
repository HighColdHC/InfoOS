"""Append-only user verification through storage, durable tasks and authenticated MCP."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.application.tasks.executor import run_due_tasks
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import TaskStatus, TaskType
from backend.application.usecases.api_tokens import create_token
from backend.application.usecases.submit_verification import submit_verification
from backend.domain.intelligence.verification import VerificationError
from backend.domain.provider_recovery import is_intelligence_evaluation
from backend.infrastructure.storage.database import default_database_path
from backend.infrastructure.storage.repositories import CardContentBlockRepository, CardRepository
from backend.read_models.card_preview import read_card_preview_detail
from backend.read_models.opportunities import list_opportunities
from backend.read_models.verifications import list_verifications
from tests.infoos.test_agent_evaluations import item as evaluation_item, seed, submit as evaluate
from tests.infoos.test_daily_intelligence import _card
from tests.infoos.test_mcp_collect import call, invoke
from tests.infoos.test_opportunity_breakdowns import START, mark, save as breakdown
from tests.infoos.test_verification_domain import item


def save(base, **changes):
    return submit_verification(base, item(**changes), token_id="writer")


def test_append_correction_blocks_and_finalize(tmp_path, monkeypatch):
    seed(tmp_path)
    blocks = CardContentBlockRepository(tmp_path)
    first = save(tmp_path, contacted_on="2026-10-14", price="2999 元")
    second = save(tmp_path, supersedes=first["verification_id"], conclusion="drop", note="交付无法验证")
    assert first["verification_id"] != second["verification_id"]
    page = list_verifications(tmp_path)
    latest, original = page["items"]
    assert latest["superseded_by"] is None and original["superseded_by"] == second["verification_id"]
    assert original["token_id"] == "writer" and original["schema_version"] == "verification_v1"
    assert original["price"] == "2999 元"
    mark(tmp_path)
    breakdown(tmp_path)
    evaluate(tmp_path, [evaluation_item()])
    breakdown(tmp_path)
    evaluate(tmp_path, [evaluation_item(quality=59)])
    rows = blocks.list_for_card("card-1")
    verified = [b for b in rows if b["block_kind"] == "opportunity_verification"]
    assert len(verified) == 2
    for block in verified:
        assert block["layer"] == "intelligence" and block["provenance"] == "user_verified.via_agent"
        assert block["raw_payload"]["purpose"] == "user_verified.via_agent"
        assert block["body"].startswith("人工核实 · 记录日期 ")
        assert not is_intelligence_evaluation(block)
    assert "接触日期：2026-10-14" in verified[0]["body"]
    assert len([b for b in read_card_preview_detail(tmp_path, "card-1")["body_blocks"]
                if b["block_kind"] == "opportunity_verification"]) == 2
    opportunity = list_opportunities(tmp_path)["items"][0]
    assert opportunity["verification_count"] == 2 and opportunity["latest_verification_conclusion"] == "drop"
    tasks = TaskRepository.for_base_dir(tmp_path).list_tasks()
    selected = [t for t in tasks if t.task_id in {first["finalize_task_id"], second["finalize_task_id"]}]
    assert len(selected) == 2 and all(t.input_version.startswith("verification:") for t in selected)
    monkeypatch.setattr("backend.application.tasks.card_delivery_transitions.enqueue_webhooks",
                        lambda *args, **kwargs: pytest.fail("verification must not enqueue webhook"))
    result = run_due_tasks(tmp_path, now="2099-01-01T00:00:00Z", task_types=[TaskType.CARD_FINALIZE])
    assert result["results"] and all(r["downstream_task_ids"] == [] for r in result["results"])
    assert all(t.status == TaskStatus.SUCCESS for t in TaskRepository.for_base_dir(tmp_path).list_tasks())
    assert CardRepository(tmp_path).get_card("card-1")["version"] == 2
    assert len([b for b in blocks.list_for_card("card-1") if b["block_kind"] == "opportunity_verification"]) == 2


def test_validation_and_atomic_rollback(tmp_path, monkeypatch):
    seed(tmp_path, profile=False)
    _card(default_database_path(tmp_path), 2, START, evaluated=False)
    first = save(tmp_path)
    for changes, code in [({"card_id": "missing"}, "card_not_found"),
                          ({"supersedes": "missing"}, "invalid_supersedes"),
                          ({"card_id": "card-2", "supersedes": first["verification_id"]}, "invalid_supersedes"),
                          ({"note": "vx:abc123"}, "contact_info_not_allowed")]:
        with pytest.raises(VerificationError, match=code):
            save(tmp_path, **changes)
    old_page = list_verifications(tmp_path)
    old_blocks = CardContentBlockRepository(tmp_path).list_for_card("card-1")
    old_tasks = TaskRepository.for_base_dir(tmp_path).list_tasks()
    import backend.application.usecases.submit_verification as module
    def fail(*args, **kwargs):
        raise RuntimeError("enqueue failed")
    monkeypatch.setattr(module, "enqueue_task", fail)
    with pytest.raises(RuntimeError, match="enqueue failed"):
        save(tmp_path)
    assert list_verifications(tmp_path) == old_page
    assert CardContentBlockRepository(tmp_path).list_for_card("card-1") == old_blocks
    assert TaskRepository.for_base_dir(tmp_path).list_tasks() == old_tasks


def test_pagination_filters_and_same_timestamp(tmp_path):
    seed(tmp_path, profile=False)
    _card(default_database_path(tmp_path), 2, START, evaluated=False)
    saved = [save(tmp_path), save(tmp_path, conclusion="drop"), save(tmp_path, card_id="card-2")]
    with sqlite3.connect(default_database_path(tmp_path)) as conn:
        conn.execute("update opportunity_verifications set created_at='2026-10-14T00:00:00.000000Z'")
    ids, cursor = [], None
    for _ in range(3):
        page = list_verifications(tmp_path, limit=1, cursor=cursor)
        ids.append(page["items"][0]["verification_id"])
        cursor = page["next_cursor"]
    assert cursor is None and ids == sorted([r["verification_id"] for r in saved], reverse=True)
    assert len(list_verifications(tmp_path, card_id="card-1")["items"]) == 2
    assert len(list_verifications(tmp_path, conclusion="drop")["items"]) == 1
    assert len(list_verifications(tmp_path, since="2026-10-14T08:00:00+08:00")["items"]) == 3
    assert list_verifications(tmp_path, since="2026-10-14T00:00:00.000001Z")["items"] == []
    cursor = list_verifications(tmp_path, limit=1)["next_cursor"]
    for args in ({"limit": 0}, {"limit": 101}, {"since": "2026-10-14"}, {"cursor": "bad"},
                 {"cursor": cursor, "card_id": "card-1"}, {"conclusion": "bad"}, {"card_id": ""}):
        with pytest.raises(ValueError):
            list_verifications(tmp_path, **args)


def test_mcp_scopes_readback_and_token_registration(tmp_path):
    seed(tmp_path, profile=False)
    reader = create_token(tmp_path, name="scheduled", scopes=["intelligence:read", "evaluation:write"])
    writer = create_token(tmp_path, name="interactive", scopes=["verification:write"])
    with TestClient(create_app(tmp_path)) as client:
        denied = invoke(client, reader, "submit_verification", **item())
        assert denied["isError"] and "insufficient_scope" in denied["content"][0]["text"]
        assert invoke(client, writer, "list_verifications")["isError"]
        for value in (True, 50.0, "50"):
            assert invoke(client, writer, "submit_verification", **item(replicability=value))["isError"]
        assert call(client, reader, "get_card", card_id="card-1")["verifications"] == []
        created = call(client, writer, "submit_verification", **item())
        assert created["deep_link"]["url"].endswith("/card-1")
        listing = call(client, reader, "list_verifications", card_id="card-1")["items"][0]
        assert listing["verification_id"] == created["verification_id"]
        assert listing["title"] and listing["platform"] and listing["deep_link"] == created["deep_link"]
        for name, args in [("submit_verification", item(note="wx:abc123")),
                           ("submit_verification", item(offer="")), ("submit_verification", item(card_id="missing")),
                           ("list_verifications", {"cursor": "bad"})]:
            result = call(client, reader if name.startswith("list") else writer, name, **args)
            assert result["error"] and result["message"]
        assert "someone@example.com" not in str(call(client, writer, "submit_verification", **item(note="someone@example.com")))
        for _ in range(20):
            save(tmp_path)
        single = call(client, reader, "get_card", card_id="card-1")
        batch = call(client, reader, "get_cards", card_ids=["card-1"])["items"][0]
        assert len(single["verifications"]) == 20 and single["verifications"] == batch["verifications"]
        response = client.post("/api/token-auth/tokens", json={"name": "interactive", "scopes": ["verification:write"]})
        assert response.status_code == 201 and response.json()["token"]["scopes"] == ["verification:write"]
        assert "verification:write" in client.get("/api/token-auth/overview").json()["capabilities"]
