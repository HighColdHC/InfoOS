"""Deterministic card IDs shared by building and indexed capture lookup."""
import hashlib

from backend.domain.research import canonical_research_item_identity


def information_card_id(platform: str, source_url: str, connector_id: str, raw_item_id: str,
                        source_id: str | None = None, source_scope: str | None = None) -> str:
    research_item = source_scope == "research"
    digest_source = (
        canonical_research_item_identity(
            platform,
            raw_item_id,
            source_url,
        )
        if research_item
        else source_url or f"{connector_id}:{raw_item_id}"
    )
    if platform == "zsxq":
        from backend.domain.sources.zsxq_digest import zsxq_topic_url
        digest_source = zsxq_topic_url(source_url, raw_item_id)
    # Maintained X feeds intentionally keep per-source card identity. Research
    # runtime sources are ephemeral batch infrastructure, so their source IDs
    # must not fragment the same X post across repeated research runs.
    if (
        platform == "x"
        and source_id
        and not research_item
    ):
        digest_source = f"{source_id}:{digest_source}"
    digest = hashlib.sha1(digest_source.encode("utf-8")).hexdigest()[:12]

    return f"card_{digest}"
