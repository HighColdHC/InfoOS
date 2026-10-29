import pytest

from backend.domain.outputs.validation import validate_output_body


def _evidence() -> list[dict[str, str]]:
    return [{"card_id": "card_1", "quote": "A concrete source fact."}]


def test_decision_brief_requires_next_action_and_evidence():
    with pytest.raises(ValueError):
        validate_output_body(
            "decision_brief",
            {"signal": "A signal", "why_it_matters": "It matters", "evidence": _evidence()},
        )


def test_content_plan_requires_hook_audience_argument_outline():
    with pytest.raises(ValueError):
        validate_output_body(
            "content_plan",
            {"hook": "Hook", "audience": "Builders", "thesis": "Thesis", "evidence": _evidence(), "takeaway": "Takeaway"},
        )


def test_opportunity_card_requires_problem_user_validation_and_risks():
    with pytest.raises(ValueError):
        validate_output_body(
            "opportunity_card",
            {
                "problem": "Problem",
                "target_user": "User",
                "current_alternative": "Alternative",
                "possible_product": "Product",
                "pricing_hypothesis": "Price",
                "missing_evidence": ["Need demand proof"],
                "next_validation": "Interview users",
                "evidence": _evidence(),
            },
        )


def test_structure_field_names_alone_are_rejected():
    with pytest.raises(ValueError):
        validate_output_body(
            "content_plan",
            {"structure": ["hook", "audience", "argument", "outline"], "evidence": _evidence()},
        )


def test_valid_output_normalizes_to_schema_payload():
    result = validate_output_body(
        "decision_brief",
        {
            "signal": "A material workflow signal",
            "why_it_matters": "It changes a current decision.",
            "next_action": "Review the source before tomorrow.",
            "risks": [],
            "evidence": _evidence(),
        },
    )

    assert result["signal"] == "A material workflow signal"
    assert result["evidence"][0]["card_id"] == "card_1"


def test_unknown_artifact_type_is_rejected():
    with pytest.raises(ValueError, match="unknown_output_artifact_type"):
        validate_output_body("unknown", {})
