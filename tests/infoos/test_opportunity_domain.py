"""Pure candidate and lens validation contracts."""
import pytest

from backend.domain.intelligence.opportunity import ENUM_MEANINGS, is_opportunity_candidate, validate_breakdown


def item(card_id="card-1", **changes):
    return {"card_id": card_id, "profile_version": 1, "monetization_model": "course", "offer": "培训课程",
            "price_signal": None, "funnel": "视频介绍课程，成交路径未知", "target_customer": None,
            "evidence": ["card_content：培训课程"], "replicability": 60, "credibility": 40,
            "risk_flags": ["unverifiable"], "next_step": "observe", "verify_questions": [],
            "breakdown_note": "有课程线索，成交与交付证据未知。", **changes}


@pytest.mark.parametrize("status,expected", [("matched", True), ("unknown", True), ("unmatched", False),
                                             ("missed", False), ("skipped", False), (None, False), ([], False)])
@pytest.mark.parametrize("nested", [False, True])
def test_candidate_prefilter(status, expected, nested):
    payload = {"prefilter": {"status": status}}
    if nested:
        payload = {"raw_metadata": payload}
    assert is_opportunity_candidate(payload, "douyin", "home") is expected


@pytest.mark.parametrize("payload,platform,source_type,expected", [
    ({}, "douyin", "home", False), ({}, "zsxq", "opportunity", True), ({}, "zsxq", "topics", False),
    ({}, "rss", "opportunity", False), ({"prefilter": None}, None, None, False),
    ({"prefilter": None, "raw_metadata": {"prefilter": {"status": "matched"}}}, None, None, False),
])
def test_candidate_source(payload, platform, source_type, expected):
    assert is_opportunity_candidate(payload, platform, source_type) is expected


@pytest.mark.parametrize("field,value,code", [
    ("monetization_model", "invalid", "invalid_monetization_model"),
    ("monetization_model", [], "invalid_monetization_model"), ("next_step", "invalid", "invalid_next_step"),
    ("offer", " ", "invalid_offer"), ("offer", "字" * 301, "offer_too_long"),
    ("price_signal", "字" * 201, "price_signal_too_long"), ("target_customer", 7, "invalid_target_customer"),
    ("target_customer", "字" * 201, "target_customer_too_long"),
    ("funnel", "字" * 501, "funnel_too_long"), ("funnel", "", "invalid_funnel"),
    ("evidence", [], "invalid_evidence"), ("evidence", ["字"] * 11, "invalid_evidence"),
    ("evidence", ["字" * 201], "evidence_too_long"), ("evidence", [1], "invalid_evidence"),
    ("evidence", [" "], "invalid_evidence"), ("replicability", True, "invalid_replicability"),
    ("replicability", -1, "invalid_replicability"), ("replicability", 101, "invalid_replicability"),
    ("credibility", 0.5, "invalid_credibility"), ("credibility", 101, "invalid_credibility"),
    ("risk_flags", ["invalid"], "invalid_risk_flags"), ("risk_flags", ["unverifiable"] * 7, "invalid_risk_flags"),
    ("verify_questions", ["字"] * 6, "invalid_verify_questions"),
    ("verify_questions", ["字" * 201], "verify_questions_too_long"),
    ("next_step", "verify", "verify_questions_required"),
    ("breakdown_note", "", "invalid_breakdown_note"), ("breakdown_note", "字" * 801, "breakdown_note_too_long"),
    ("breakdown_note", "english only", "breakdown_note_requires_chinese"),
], ids=lambda value: str(value)[:35])
def test_invalid_fields(field, value, code):
    with pytest.raises(ValueError, match=f"^opportunity_{code}$"):
        validate_breakdown(item(**{field: value}))


def test_valid_enums_boundaries_and_trimming():
    for field in ("monetization_model", "next_step"):
        for value in ENUM_MEANINGS[field]:
            result = validate_breakdown(item(**{field: f" {value} "}, verify_questions=[" 问题 "]))
            assert result[field] == value and result["verify_questions"] == ["问题"]
    result = validate_breakdown(item(offer=" " + "字" * 300 + " ", funnel="字" * 500,
        price_signal="字" * 200, target_customer="字" * 200, evidence=[" 字 "], replicability=0, credibility=100,
        risk_flags=list(ENUM_MEANINGS["risk_flags"]), breakdown_note="字" * 800))
    assert result["offer"] == "字" * 300 and result["evidence"] == ["字"]
