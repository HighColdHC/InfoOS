from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.domain.feishu_content import combine_post_document, compact_feishu_evidence, feishu_metadata, keeps_previous_body, original_post
from backend.domain.external_doc_policy import external_doc_urls, fetch_hydration, has_external_doc_hint
from backend.infrastructure.storage.feishu_backfill import FeishuBackfillRepository

from backend.application.enrichment.external_doc_inputs import external_doc_inputs
from backend.application.enrichment.external_doc_media import persist_authenticated_external_media
from backend.infrastructure.external_docs.feishu_browser import (
    FeishuDocumentResult,
    _bounded_media_urls,
    fetch_feishu_document,
)
from backend.infrastructure.storage.repositories import CardContentBlockRepository, CardRepository
from backend.infrastructure.storage.raw_media import RawMediaStore
from backend.infrastructure.storage.paths import resolve_data_dir

def enrich_external_docs(card_id: str, base_dir: Path | str, card: dict[str, Any] | None = None, *, document_url: str = "",
                         force_browser: bool = False) -> int:
    if card is None:
        card = CardRepository(base_dir).get_card(card_id)
    if not card or card["source_platform"] not in {"scys", "zsxq"}:
        return 0
    card, raw, blocks = external_doc_inputs(card, base_dir)
    urls = external_doc_urls(card, blocks, document_url=document_url)
    if not urls and not has_external_doc_hint(card):
        return 0
    if urls:
        hydration = None if force_browser else fetch_hydration(raw, urls[0], blocks)
        fetched = _hydrated_result(hydration, base_dir) if hydration else fetch_feishu_document(urls[0])
        if fetched.status == "fetched":
            persist_authenticated_external_media(
                card["card_id"],
                fetched.source_url,
                fetched.media_blobs,
                base_dir,
            )
        status = "body_fetched" if fetched.status == "fetched" else fetched.status
        _upsert_external_doc_block(
            card,
            base_dir,
            status=status,
            url=urls[0],
            urls=urls,
            fetched=fetched,
            read_via="fetch_hydration" if hydration else "browser",
        )
        _publish_document_body(card, base_dir, fetched)
    else:
        _upsert_external_doc_block(card, base_dir, status="blocked", url=card["source_url"], urls=[])
    return 1


def _hydrated_result(hydration: dict[str, Any], base_dir: Path | str) -> FeishuDocumentResult:
    blobs = RawMediaStore(resolve_data_dir(base_dir)).load(hydration["media_blobs"])
    body = hydration["body"]
    return FeishuDocumentResult(
        status="fetched", source_url=hydration["source_url"], body=body, complete=True,
        blocks=max(0, int(hydration["blocks"] or 0)),
        reached_end=hydration["reached_end"] is True,
        images=_bounded_media_urls(hydration["images"]),
        videos=_bounded_media_urls(hydration["videos"]), media_blobs=blobs,
        diagnostics={"body_chars": len(body), "authenticated_media_count": len(blobs),
                     "authenticated_media_bytes": sum(len(blob.content) for blob in blobs)},
    )


def _upsert_external_doc_block(
    card: dict[str, Any],
    base_dir: Path | str,
    *,
    status: str,
    url: str,
    urls: list[str],
    fetched: FeishuDocumentResult | None = None,
    read_via: str | None = None,
) -> None:
    repo = CardContentBlockRepository(base_dir)
    card_id = card["card_id"]
    block_id = f"block_{card_id}_external_doc_feishu"
    existing = next(
        (block for block in repo.list_for_card(card_id) if block["block_id"] == block_id),
        None,
    )
    existing_payload = (
        existing["raw_payload"]
        if existing and isinstance(existing["raw_payload"], dict)
        else {}
    )
    url_found = bool(urls)
    body = fetched.body.strip() if fetched and fetched.status == "fetched" else (
        "检测到飞书/Lark 外部文档链接，但当前浏览器会话尚未取得可见正文。"
        if url_found
        else "原文提到飞书好读版或飞书文档，但生财有术/OpenCLI 输出未暴露可访问链接。"
    )
    block_reason = fetched.reason if fetched else (None if url_found else "external_doc_url_missing")
    complete = bool(fetched and fetched.status == "fetched" and fetched.complete)
    retained_body = bool(existing and (existing_payload.get("fetch_state") == "fetched" or existing_payload.get("retained_body")))
    if retained_body and keeps_previous_body(len(body), len(existing["body"]), complete=complete):
        body = existing["body"]
        complete = False
        block_reason = block_reason or "external_doc_shorter_result"
    if fetched and fetched.status == "fetched" and not complete:
        block_reason = block_reason or "external_doc_partial"
    retry_count = int(existing_payload.get("retry_count") or 0)
    if fetched is not None and fetched.status != "fetched":
        retry_count += 1
    authenticated_media_retry_count = int(
        existing_payload.get("authenticated_media_retry_count") or 0
    )
    if (
        fetched is not None
        and fetched.status == "fetched"
        and (fetched.images or fetched.videos)
        and not fetched.media_blobs
    ):
        authenticated_media_retry_count += 1
    repo.upsert_block(
        {
            "block_id": block_id,
            "card_id": card_id,
            "position": existing["position"] if existing else _next_block_position(repo, card_id),
            "layer": "external",
            "block_kind": "external_doc",
            "status": status,
            "body": body,
            "segments": [],
            "source_url": url,
            "provenance": "Shengcai external document detection",
            "raw_payload": {
                "provider": "feishu",
                "read_via": read_via,
                "completeness": "complete" if complete else "partial",
                "retained_body": retained_body or bool(fetched and fetched.status == "fetched"),
                "blocks": fetched.blocks if fetched else 0,
                "reached_end": fetched.reached_end if fetched else False,
                "document_url": url,
                "resolved_url": fetched.source_url if fetched else None,
                "images": list(fetched.images) if fetched else [],
                "videos": list(fetched.videos) if fetched else [],
                "detection": {
                    "url_found": url_found,
                    "url_candidates": urls,
                    "source_platform": card["source_platform"],
                    "topic_id": card["raw_payload"].get("topic_id") if isinstance(card["raw_payload"], dict) else None,
                },
                "block_reason": block_reason,
                "retry_count": retry_count,
                "read_attempts": int(existing_payload.get("read_attempts") or 0) + 1,
                "authenticated_media_retry_count": authenticated_media_retry_count,
                "fetch_state": (
                    "fetched"
                    if fetched and fetched.status == "fetched"
                    else fetched.status if fetched else "blocked_missing_url"
                ),
                "access_constraints": (
                    ["authentication_may_be_required", "dynamic_rendering_may_be_required"]
                    if url_found
                    else []
                ),
                "next_action": (
                    "none"
                    if complete
                    else "retry_with_authenticated_container_browser"
                    if url_found
                    else "manual_open_source_or_wait_for_scys_adapter_to_expose_link"
                ),
                "diagnostics": dict(fetched.diagnostics) if fetched else {},
            },
        }
    )


def _next_block_position(repo: CardContentBlockRepository, card_id: str) -> int:
    return max((block["position"] for block in repo.list_for_card(card_id)), default=-1) + 1


def _publish_document_body(card: dict[str, Any], base_dir: Path | str, fetched: FeishuDocumentResult) -> None:
    if card["source_platform"] != "zsxq":
        return
    payload = card["raw_payload"]
    post = original_post(payload)
    old_body = card["content"]
    content = combine_post_document(post, fetched.body) if fetched.status == "fetched" else old_body
    status = ("fetched" if fetched.complete else "partial") if fetched.status == "fetched" else (
        "login_required" if fetched.reason == "external_doc_login_required" else "empty"
    )
    reason = fetched.reason
    if keeps_previous_body(len(content), len(old_body), complete=status == "fetched"):
        content = old_body
        if status == "fetched":
            status, reason = "partial", "external_doc_shorter_result"
    previous = compact_feishu_evidence(feishu_metadata(payload))
    evidence = {**previous, "content_source": "feishu", "content_source_status": status,
                "content_source_url": fetched.source_url, "original_content": post,
                "content_source_completeness": "complete" if status == "fetched" else "partial",
                "reason": reason, "blocks": fetched.blocks, "reached_end": fetched.reached_end}
    FeishuBackfillRepository(base_dir).publish_read(card["card_id"], content=content, evidence=evidence)
