from dataclasses import replace

import pytest

from backend.domain.insights import (
    InvalidCardInsightError,
    SummaryPromptSnapshot,
    is_valid_insight_row,
    summary_snapshot_version,
    validate_insight_core,
)


def _snapshot() -> SummaryPromptSnapshot:
    return SummaryPromptSnapshot(
        title="Title", content="Content", existing_summary="Summary",
        enriched_context="Context", context_gaps="Gap",
        visible_domain_names=("A", "B"),
        interpretation_version="profile-v1", interpretation_prompt="Focus prompt",
        model="model-a", prompt_version="summary-v2",
        fallback_scores=(("creation", 1), ("insight", 2), ("relevance", 3), ("timeliness", 4)),
    )


def test_insight_core_validity_is_trimmed_and_status_aware():
    assert validate_insight_core(" Main ", " Why ") == ("Main", "Why")
    assert is_valid_insight_row({"status": "succeeded", "main_point": " Main ", "why_it_matters": " Why "})
    assert not is_valid_insight_row({"status": "failed", "main_point": "Main", "why_it_matters": "Why"})
    with pytest.raises(InvalidCardInsightError):
        validate_insight_core(" ", "Why")


def test_summary_snapshot_version_is_deterministic_and_tracks_prompt_inputs():
    snapshot = _snapshot()
    assert summary_snapshot_version(snapshot) == summary_snapshot_version(snapshot)
    for changed in (
        replace(snapshot, visible_domain_names=("C",)),
        replace(snapshot, interpretation_version="profile-v2"),
        replace(snapshot, interpretation_prompt="Different prompt"),
        replace(snapshot, model="model-b"),
        replace(snapshot, prompt_version="summary-v3"),
        replace(snapshot, fallback_scores=(("creation", 5),)),
    ):
        assert summary_snapshot_version(changed) != summary_snapshot_version(snapshot)
