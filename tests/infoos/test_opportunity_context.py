"""S1 profile provenance, frozen verification context and submission identity."""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.application.tasks.executor import run_due_tasks
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import TaskType
from backend.application.usecases.api_tokens import create_token
from backend.application.usecases.interest_profiles import push_profile
from backend.application.usecases.submit_verification import submit_verification
from backend.domain.intelligence.opportunity import breakdown_snapshot
from backend.domain.intelligence.verification import FIELDS, VerificationError, validate_verification
from backend.infrastructure.storage.database import default_database_path
from backend.infrastructure.storage.migrations.runner import LATEST_VERSION, migrate_database
from backend.infrastructure.storage.opportunity_breakdowns import COLUMNS, OpportunityBreakdownRepository
from backend.infrastructure.storage.repositories import CardContentBlockRepository, CardRepository
from backend.read_models.opportunities import list_opportunities, list_opportunity_candidates
from backend.read_models.verifications import list_verifications
from tests.infoos.test_agent_evaluations import PROFILE, seed
from tests.infoos.test_daily_intelligence import _card
from tests.infoos.test_mcp_collect import call
from tests.infoos.test_opportunity_breakdowns import START, mark, save
from tests.infoos.test_opportunity_domain import item as breakdown_item
from tests.infoos.test_verification_domain import item


def latest(base):
    return OpportunityBreakdownRepository(base).latest_for_card("card-1")


def linked(base, **changes):
    current = latest(base)
    return item(breakdown_id=current["breakdown_id"],
                expected_breakdown_hash=current["breakdown_content_hash"], **changes)


def counts(base):
    with CardRepository(base).connect() as conn:
        return tuple(conn.execute(f"select count(*) from {table}").fetchone()[0]
                     for table in ("opportunity_verifications", "card_content_blocks", "tasks"))


def test_profile_lifecycle_and_raw_revision_after_finalize(tmp_path):
    seed(tmp_path)
    mark(tmp_path)
    assert save(tmp_path)[0]["status"] == "accepted"
    first = latest(tmp_path)
    assert first["profile_version"] == 1 and first["profile_is_current"]
    assert first["raw_item_revision"] == 0
    assert list_opportunity_candidates(tmp_path, START)["items"] == []
    run = run_due_tasks(tmp_path, now="2099-01-01T00:00:00Z", task_types=[TaskType.CARD_FINALIZE])
    assert run["results"]
    assert CardRepository(tmp_path).get_card("card-1")["version"] == 2
    assert list_opportunity_candidates(tmp_path, START)["items"] == []
    push_profile(tmp_path, PROFILE.replace("InfoOS", "商机"), source="test")
    outdated = latest(tmp_path)
    assert not outdated["profile_is_current"] and outdated["profile_version"] == 1
    assert outdated["breakdown_content_hash"] == first["breakdown_content_hash"]
    page = list_opportunity_candidates(tmp_path, START)
    assert page["profile_version"] == 2 and page["items"][0]["card_id"] == "card-1"
    assert not list_opportunities(tmp_path)["items"][0]["profile_is_current"]
    assert save(tmp_path)[0]["error"] == "profile_version_mismatch"
    assert save(tmp_path, [breakdown_item(profile_version=2)])[0]["status"] == "accepted"
    assert latest(tmp_path)["profile_is_current"]
    assert list_opportunity_candidates(tmp_path, START)["items"] == []
    with CardRepository(tmp_path).connect() as conn:
        conn.execute("update raw_items set revision=revision+1 where raw_item_id='card-1'")
    assert list_opportunity_candidates(tmp_path, START)["items"][0]["card_id"] == "card-1"


def test_profile_required_validation_unknown_and_cursor(tmp_path):
    seed(tmp_path, profile=False)
    mark(tmp_path)
    assert save(tmp_path)[0]["error"] == "profile_required"
    assert list_opportunity_candidates(tmp_path, START)["profile_version"] is None
    push_profile(tmp_path, PROFILE, source="test")
    for value in (None, True, 0, -1, "1", 1.0):
        assert save(tmp_path, [breakdown_item(profile_version=value)])[0]["error"] == "invalid_profile_version"
    missing = breakdown_item()
    del missing["profile_version"]
    assert save(tmp_path, [missing])[0]["error"] == "invalid_profile_version"
    save(tmp_path)
    with CardRepository(tmp_path).connect() as conn:
        conn.execute("update opportunity_breakdowns set profile_version=null, raw_item_revision=null")
    assert not latest(tmp_path)["profile_is_current"]
    assert list_opportunity_candidates(tmp_path, START)["items"]
    _card(default_database_path(tmp_path), 2, START, evaluated=False)
    mark(tmp_path, 2)
    cursor = list_opportunity_candidates(tmp_path, START, limit=1)["next_cursor"]
    push_profile(tmp_path, PROFILE.replace("InfoOS", "商机"), source="test")
    with pytest.raises(ValueError, match="invalid_cursor"):
        list_opportunity_candidates(tmp_path, START, limit=1, cursor=cursor)


def test_snapshot_overwrite_replay_and_published_card_link(tmp_path):
    seed(tmp_path)
    mark(tmp_path)
    save(tmp_path, [breakdown_item(next_step="verify", verify_questions=["是否交付？"])])
    a = latest(tmp_path)
    payload = linked(tmp_path, client_verification_id="confirmed-A")
    first = submit_verification(tmp_path, payload, token_id="writer")
    assert first["replayed"] is False
    before = counts(tmp_path)
    # Same key preserves its ID and creation time but changes the judgment and hash.
    save(tmp_path, [breakdown_item(offer="判断B", next_step="skip")])
    b = latest(tmp_path)
    assert b["breakdown_id"] == a["breakdown_id"] and b["breakdown_content_hash"] != a["breakdown_content_hash"]
    after_overwrite = counts(tmp_path)
    replay = submit_verification(tmp_path, {**payload, "offer": " 四周课程 "}, token_id="writer")
    assert replay == {**first, "replayed": True}
    assert counts(tmp_path) == after_overwrite
    assert before[:2] == after_overwrite[:2]
    history = list_verifications(tmp_path)["items"]
    assert len(history) == 1 and history[0]["breakdown_snapshot"] == breakdown_snapshot(a)
    body = next(b["body"] for b in CardContentBlockRepository(tmp_path).list_for_card("card-1")
                if b["block_kind"] == "opportunity_verification")
    assert "值得接触核实" in body and "画像 1" in body
    with pytest.raises(VerificationError, match="breakdown_changed"):
        submit_verification(tmp_path, {**payload, "client_verification_id": "new-attempt"}, token_id="writer")
    run_due_tasks(tmp_path, now="2099-01-01T00:00:00Z", task_types=[TaskType.CARD_FINALIZE])
    assert latest(tmp_path)["card_version"] < CardRepository(tmp_path).get_card("card-1")["version"]
    assert submit_verification(tmp_path, linked(tmp_path), token_id="writer")["replayed"] is False


def test_identity_conflicts_independent_tokens_and_invalid_links(tmp_path):
    seed(tmp_path)
    mark(tmp_path)
    save(tmp_path)
    payload = linked(tmp_path, client_verification_id="same-id")
    first = submit_verification(tmp_path, payload, token_id="writer")
    for changes in ({"note": "新情况"}, {"expected_breakdown_hash": "changed"}, {"offer": "另一课程"}):
        with pytest.raises(VerificationError, match="verification_conflict"):
            submit_verification(tmp_path, {**payload, **changes}, token_id="writer")
    assert submit_verification(tmp_path, payload, token_id="other")["verification_id"] != first["verification_id"]
    assert submit_verification(tmp_path, {**payload, "client_verification_id": "new"}, token_id="writer")["verification_id"] != first["verification_id"]
    _card(default_database_path(tmp_path), 2, START, evaluated=False)
    for changes in ({"breakdown_id": "missing"}, {"card_id": "card-2"}, {"expected_breakdown_hash": None}):
        with pytest.raises(VerificationError, match="invalid_breakdown"):
            submit_verification(tmp_path, {**payload, "client_verification_id": "invalid", **changes}, token_id="writer")
    with CardRepository(tmp_path).connect() as conn:
        conn.execute("update cards set version=2 where card_id='card-1'")
    save(tmp_path)
    with pytest.raises(VerificationError, match="breakdown_changed"):
        submit_verification(tmp_path, {**payload, "client_verification_id": "outdated"}, token_id="writer")
    plain = submit_verification(tmp_path, item(), token_id="writer")
    row = next(r for r in list_verifications(tmp_path)["items"] if r["verification_id"] == plain["verification_id"])
    assert row["breakdown_snapshot"] is None
    assert "未关联 AI 拆解" in next(b["body"] for b in CardContentBlockRepository(tmp_path).list_for_card("card-1")
                              if b["block_id"] == plain["verification_id"] + "_block")


@pytest.mark.parametrize("value", [None, "", " ", "x" * 129, 1])
def test_client_id_required_and_bounded(value):
    with pytest.raises(VerificationError) as error:
        validate_verification(item(client_verification_id=value))
    assert error.value.field == "client_verification_id"


def test_mcp_profile_snapshot_and_exact_replay(tmp_path):
    seed(tmp_path)
    mark(tmp_path)
    save(tmp_path)
    token = create_token(tmp_path, name="interactive", scopes=["intelligence:read", "verification:write"])
    with TestClient(create_app(tmp_path)) as client:
        assert call(client, token, "get_opportunity_lens")["profile_version"] == 1
        card = call(client, token, "get_card", card_id="card-1")
        assert card["opportunity"]["profile_version"] == 1 and card["opportunity"]["profile_is_current"]
        request = linked(tmp_path)
        created = call(client, token, "submit_verification", **request)
        before = counts(tmp_path)
        assert call(client, token, "submit_verification", **request) == {**created, "replayed": True}
        assert counts(tmp_path) == before
        single = call(client, token, "get_card", card_id="card-1")
        batch = call(client, token, "get_cards", card_ids=["card-1"])["items"][0]
        listing = call(client, token, "list_verifications")["items"][0]
        assert single["verifications"] == batch["verifications"]
        snapshot = single["verifications"][0]["breakdown_snapshot"]
        assert snapshot == listing["breakdown_snapshot"] == breakdown_snapshot(card["opportunity"])
        tasks = [t for t in TaskRepository.for_base_dir(tmp_path).list_tasks()
                 if t.input_version.startswith("verification:")]
        assert len(tasks) == 1


def test_v13_data_upgrade_and_repeat_preserve_unknown_history(tmp_path):
    db = tmp_path / "v13.sqlite3"
    def stop(event):
        if event["migration"] == 14:
            raise RuntimeError("pre-v14")
    with pytest.raises(RuntimeError, match="pre-v14"):
        migrate_database(db, progress_callback=stop)
    _card(db, 1, START, evaluated=False)
    old_breakdown = {**breakdown_item(), "breakdown_id": "old-b", "card_version": 1,
                     "lens_version": "opportunity_v1", "rules_version": "old", "evaluator": "old",
                     "token_id": "writer", "created_at": START}
    columns = [c for c in COLUMNS if c not in {"profile_version", "raw_item_revision", "writeback_at"}]
    with sqlite3.connect(db) as conn:
        assert conn.execute("pragma user_version").fetchone()[0] == 13
        conn.execute(f"insert into opportunity_breakdowns ({','.join(columns)}) values ({','.join('?' for _ in columns)})",
                     [json.dumps(old_breakdown[c]) if isinstance(old_breakdown[c], list) else old_breakdown[c] for c in columns])
        old_record = {**validate_verification(item()), "verification_id": "old-v", "token_id": "writer",
                      "schema_version": "verification_v1", "created_at": START}
        columns = [*FIELDS, "verification_id", "token_id", "schema_version", "created_at"]
        conn.execute(f"insert into opportunity_verifications ({','.join(columns)}) values ({','.join('?' for _ in columns)})",
                     [old_record[c] for c in columns])
    assert migrate_database(db).after_version == LATEST_VERSION
    assert migrate_database(db).before_version == LATEST_VERSION
    with sqlite3.connect(db) as conn:
        assert conn.execute("select breakdown_id,profile_version,raw_item_revision from opportunity_breakdowns").fetchall() == [("old-b", None, None)]
        assert conn.execute("select verification_id,client_verification_id,request_content_hash,breakdown_id,expected_breakdown_hash,breakdown_snapshot from opportunity_verifications").fetchall() == [("old-v", None, None, None, None, None)]
        assert conn.execute("pragma quick_check").fetchone()[0] == "ok"
        index = next(r for r in conn.execute("pragma index_list(opportunity_verifications)") if r[1] == "idx_opportunity_verifications_client")
        assert index[2] == 1
        assert [r[2] for r in conn.execute("pragma index_info(idx_opportunity_verifications_client)")] == ["token_id", "client_verification_id"]
