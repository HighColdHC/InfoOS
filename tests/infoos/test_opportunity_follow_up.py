"""Stage derivation, atomic decisions and interactive MCP permissions."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.application.usecases.api_tokens import create_token
from backend.application.usecases.opportunity_follow_up import set_opportunity_follow_up
from backend.domain.intelligence.opportunity_follow_up import derive_stage
from backend.infrastructure.storage.opportunity_follow_ups import OpportunityFollowUpRepository
from tests.infoos.test_agent_evaluations import seed
from tests.infoos.test_mcp_collect import call, invoke
from tests.infoos.test_opportunity_breakdowns import mark, save
from tests.infoos.test_opportunity_domain import item

T0, T1, T2 = "2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z", "2026-10-03T00:00:00Z"


@pytest.mark.parametrize("state,conclusion,newer,step,observe,expected,archive", [
    (None, None, False, "verify", False, "inbox", None),
    (None, None, False, "observe", False, None, None),
    (None, None, False, "observe", True, "inbox", None),
    (None, None, False, "skip", True, None, None),
    ("following", None, False, "skip", False, "plan", None),
    ("dismissed", None, False, "verify", False, "archive", "dismissed"),
    ("reset", None, False, "verify", False, "inbox", None),
    ("following", "keep_observing", False, "verify", False, "contact", None),
    ("following", "to_action", False, "verify", False, "action", None),
    ("following", "drop", False, "verify", False, "archive", "dropped"),
    ("following", "to_action", True, "verify", False, "plan", None),
    ("dismissed", "keep_observing", True, "verify", False, "archive", "dismissed"),
    ("reset", "to_action", True, "verify", False, "action", None),
])
def test_stage(state, conclusion, newer, step, observe, expected, archive):
    follow = {"state": state, "created_at": T2 if newer else T1} if state else None
    verification = {"conclusion": conclusion, "created_at": T1 if newer else T2} if conclusion else None
    result = derive_stage(step, T0, follow, verification, observe,
                          now=datetime(2026, 10, 4, tzinfo=timezone.utc))
    assert result["stage"] == expected and result["archive_reason"] == archive
    if expected == "inbox":
        assert result["stage_since"] == T0


@pytest.mark.parametrize("stage,days,stale", [("plan", 3, False), ("plan", 4, True),
                                             ("contact", 14, False), ("contact", 15, True)])
def test_staleness(stage, days, stale):
    follow = {"state": "following", "created_at": T0} if stage == "plan" else None
    verification = {"conclusion": "keep_observing", "created_at": T0} if stage == "contact" else None
    assert derive_stage("verify", T0, follow, verification,
        now=datetime(2026, 10, 1 + days, tzinfo=timezone.utc))["stale"] is stale


def prepared(base):
    seed(base)
    mark(base)
    save(base, [item(next_step="verify", verify_questions=["交付是什么？"])])


def test_write_validation_idempotence_and_mcp(tmp_path):
    seed(tmp_path)
    for card_id, expected in [("missing", "card_not_found"), ("card-1", "opportunity_breakdown_required")]:
        with pytest.raises(ValueError, match=expected):
            set_opportunity_follow_up(tmp_path, card_id, "following", actor="web")
    mark(tmp_path)
    save(tmp_path, [item(next_step="verify", verify_questions=["价格？"])])
    for state, reason, code in [("contact", None, "invalid_follow_up_state"),
        ("action", None, "invalid_follow_up_state"), ("following", "字" * 201, "follow_up_reason_too_long")]:
        with pytest.raises(ValueError, match=code):
            set_opportunity_follow_up(tmp_path, "card-1", state, reason, actor="web")
    first = set_opportunity_follow_up(tmp_path, "card-1", "following", "  想了解  ", actor="web")
    assert first["stage"] == "plan"
    assert set_opportunity_follow_up(tmp_path, "card-1", "following", actor="web") == first
    events = OpportunityFollowUpRepository(tmp_path).for_card("card-1")
    assert len(events) == 1 and events[0]["reason"] == "想了解"
    app = create_app(tmp_path)
    reader = create_token(tmp_path, scopes=["intelligence:read"], name="scheduled")
    writer = create_token(tmp_path, scopes=["verification:write"], name="interactive")
    with TestClient(app) as client:
        assert invoke(client, reader, "set_opportunity_follow_up", card_id="card-1", state="dismissed")["isError"]
        result = call(client, writer, "set_opportunity_follow_up", card_id="card-1", state="dismissed")
        assert result["stage"] == "archive"
        opportunity = call(client, reader, "list_opportunities")["items"][0]
        assert opportunity["follow_up"]["state"] == "dismissed"
    assert OpportunityFollowUpRepository(tmp_path).for_card("card-1")[-1]["actor"].startswith("mcp:")
