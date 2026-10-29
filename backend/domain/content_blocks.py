"""Stable vocabulary for persisted, user-facing content blocks.

The vocabulary documents kinds produced by InfoOS. Storage intentionally keeps
unknown non-blank values so newer producers remain readable by older clients.
"""

from __future__ import annotations


USER_FACING_CONTENT_BLOCK_KINDS = frozenset(
    {
        "analysis",
        "author_profile",
        "card_content",
        "enrichment",
        "enrichment_gap",
        "external_doc",
        "image",
        "image_description",
        "image_ocr",
        "intel_summary",
        "opportunity_breakdown",
        "opportunity_verification",
        "linked_article",
        "llm_context",
        "media_download_gap",
        "metadata",
        "original_tweet",
        "platform_comments",
        "quote",
        "quoted_tweet",
        "reddit_metrics",
        "reddit_outbound_link",
        "reddit_post",
        "reply_parent",
        "repost_original",
        "summary",
        "screening_evidence",
        "relation_note",
        "thread",
        "transcript",
        "translation",
        "tweet",
        "video",
        "video_transcript",
        "x_article",
    }
)


def normalize_content_block_kind(value: object) -> str:
    """Return a safe persisted kind without rejecting forward-compatible kinds."""

    kind = str(value).strip() if value is not None else ""
    if not kind:
        raise ValueError("block_kind must not be blank")
    return kind
