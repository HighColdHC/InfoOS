"""Read article pages before the existing external-document enrichment step."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.application.enrichment.external_doc_inputs import external_doc_inputs
from backend.domain.feishu_content import combine_post_document
from backend.domain.timestamps import utc_now_seconds_z
from backend.domain.zsxq_article import ARTICLE_POLICY_VERSION, article_evidence, article_gap, article_url
from backend.infrastructure.external_docs.zsxq_article_browser import fetch_zsxq_article
from backend.infrastructure.storage.runtime_circuits import RuntimeCircuitRepository
from backend.infrastructure.storage.zsxq_article import ZsxqArticleRepository


class ArticleCircuitOpen(RuntimeError):
    def __init__(self, retry_at: str | None):
        super().__init__("zsxq_article_platform_circuit_open")
        self.retry_at = retry_at


def enrich_zsxq_article(card: dict[str, Any], base_dir: Path | str) -> None:
    if card["source_platform"] != "zsxq":
        return
    card, raw, _ = external_doc_inputs(card, base_dir)
    if not article_gap(card):
        return
    url = article_url(card)
    previous = article_evidence(raw) if raw is not None else article_evidence(card)
    evidence = {**previous, "article_url": url, "policy_version": ARTICLE_POLICY_VERSION,
                "read_attempts": previous.get("read_attempts", 0) + 1, "read_at": utc_now_seconds_z()}
    repo = ZsxqArticleRepository(base_dir)
    circuit = RuntimeCircuitRepository(base_dir).get("platform", "zsxq")
    if circuit and circuit["state"] != "closed":
        repo.publish_read(card["card_id"], evidence={**evidence, "status": "pending", "reason": "platform_circuit_open"},
                          links=[], images=[])
        raise ArticleCircuitOpen(circuit["cooldown_until"])
    result = fetch_zsxq_article(url)
    status = "partial" if result.status == "fetched" and not result.complete else result.status
    evidence.update(status=status, article_title=result.title, diagnostics=result.diagnostics)
    post = ""
    if result.status == "fetched":
        # An article title is authoritative; the card title may itself be the entire teaser.
        post = combine_post_document(result.title, result.body)
        evidence["original_content"] = post
    repo.publish_read(card["card_id"], evidence=evidence, post=post, links=result.links, images=result.images)
    if result.status == "failed":
        raise RuntimeError("zsxq_article_browser_failed")
