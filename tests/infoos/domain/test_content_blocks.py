from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.domain.content_blocks import (
    USER_FACING_CONTENT_BLOCK_KINDS,
    normalize_content_block_kind,
)


def test_user_facing_content_block_kinds_cover_current_enrichment_contract() -> None:
    assert {
        "reddit_post",
        "reddit_metrics",
        "reddit_outbound_link",
        "platform_comments",
        "translation",
        "external_doc",
        "llm_context",
        "enrichment_gap",
        "media_download_gap",
    } <= USER_FACING_CONTENT_BLOCK_KINDS


def test_frontend_and_backend_known_kind_vocabularies_stay_in_sync() -> None:
    source = (Path(__file__).parents[3] / "frontend/src/lib/types/common.ts").read_text()
    match = re.search(
        r"KNOWN_CONTENT_BLOCK_KINDS\s*=\s*(\[.*?\])\s*as const",
        source,
        re.DOTALL,
    )

    assert match is not None
    frontend_kinds = set(re.findall(r'"([a-z0-9_]+)"', match.group(1)))
    assert frontend_kinds == USER_FACING_CONTENT_BLOCK_KINDS


def test_content_block_kind_contract_preserves_unknown_kind_for_forward_compatibility() -> None:
    assert normalize_content_block_kind("future_rich_embed") == "future_rich_embed"


@pytest.mark.parametrize("value", ["", "   "])
def test_content_block_kind_contract_rejects_blank_kind(value: str) -> None:
    with pytest.raises(ValueError, match="block_kind must not be blank"):
        normalize_content_block_kind(value)
