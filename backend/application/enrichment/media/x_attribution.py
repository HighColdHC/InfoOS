from __future__ import annotations

from pathlib import Path
from typing import Any
from collections import Counter

from backend.application.enrichment.media.x_urls import is_x_tweet_media_url, parse_canonical_x_status_url
from backend.infrastructure.storage.repositories import CardAssetRepository, CardContentBlockRepository

X_MEDIA_CROSS_CARD_ACCOUNT_THRESHOLD = 3


def _attribute_opencli_files(
    entries: Any, staged: list[dict[str, Any]], tweet_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Map each successful OpenCLI row to one tweet_<index> file."""
    files_by_stem: dict[str, list[dict[str, Any]]] = {}
    for item in staged:
        files_by_stem.setdefault(item["path"].stem, []).append(item)
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    used_stems: set[str] = set()
    index_counts = Counter(
        row.get("index") for row in entries if isinstance(row, dict) and isinstance(row.get("index"), int)
    ) if isinstance(entries, list) else Counter()
    for row in entries if isinstance(entries, list) else []:
        if not isinstance(row, dict):
            rejected.append({"reason": "invalid_row"})
            continue
        index = row.get("index")
        if not isinstance(index, int) or isinstance(index, bool) or index < 1:
            rejected.append({"reason": "invalid_index"})
            continue
        stem = f"tweet_{index}"
        files = files_by_stem.get(stem, [])
        url = str(row.get("url") or "")
        kind = str(row.get("type") or "")
        valid = (
            index_counts[index] == 1
            and str(row.get("tweet_id") or "") == tweet_id
            and row.get("status") == "success"
            and is_x_tweet_media_url(url)
            and kind in {"image", "video"}
            and len(files) == 1
            and files[0]["kind"] == kind
            and files[0]["path"].suffix.lower() == (".jpg" if kind == "image" else ".mp4")
        )
        used_stems.add(stem)
        if valid:
            accepted.append({**files[0], "remote_url": url})
        else:
            rejected.append({"index": index, "reason": "row_file_mismatch"})
    for stem, files in files_by_stem.items():
        if stem not in used_stems:
            rejected.extend({"file": item["path"].name, "reason": "no_output_row"} for item in files)
    if not isinstance(entries, list):
        rejected.append({"reason": "invalid_output_rows"})
    return accepted, rejected


def _persist_x_opencli_remote_urls(asset_repo: Any, card_id: str, files: list[dict[str, Any]]) -> None:
    urls_by_path = {item["relative_path"]: item["remote_url"] for item in files}
    for asset in asset_repo.list_for_card(card_id):
        url = urls_by_path.get(asset["local_path"])
        if url:
            asset_repo.upsert_asset({
                **asset,
                "remote_url": url,
                "raw_payload": {**asset["raw_payload"], "remote_url": url},
            })


def _cross_card_duplicate(
    base_dir: Path, card_id: str, tweet_url: str, digest: str,
) -> dict[str, Any] | None:
    owners = CardAssetRepository(base_dir).list_x_cards_with_hash(
        f"sha256:{digest}", exclude_card_id=card_id,
    )
    accounts = {
        status.handle.lower()
        for row in owners
        if (status := parse_canonical_x_status_url(row["source_url"])) is not None
    }
    if len(accounts) < X_MEDIA_CROSS_CARD_ACCOUNT_THRESHOLD:
        return None
    block_repo = CardContentBlockRepository(base_dir)
    current_relations = _x_relation_tweet_ids(block_repo.list_for_card(card_id))
    current_status = parse_canonical_x_status_url(tweet_url)
    if current_status is None:
        return None
    for owner in owners:
        prior_status = parse_canonical_x_status_url(owner["source_url"])
        if prior_status is None:
            continue
        if prior_status.tweet_id in current_relations:
            return None
        if current_status.tweet_id in _x_relation_tweet_ids(block_repo.list_for_card(owner["card_id"])):
            return None
    return {"content_hash": f"sha256:{digest}", "account_count": len(accounts), "card_ids": [row["card_id"] for row in owners[:20]]}


def _x_relation_tweet_ids(blocks: list[dict[str, Any]]) -> set[str]:
    ids: set[str] = set()
    roles = {"quote": "quoted", "repost_original": "retweeted"}
    for block in blocks:
        role = roles.get(block["block_kind"])
        payload = block["raw_payload"]
        if not role or block["layer"] != "context" or not isinstance(payload, dict):
            continue
        if payload.get("content_role") != role or payload.get("relation_depth") != 1:
            continue
        block_status = parse_canonical_x_status_url(block["source_url"] or "")
        payload_status = parse_canonical_x_status_url(str(payload.get("relation_source_url") or ""))
        if block_status and payload_status and block_status.tweet_id == payload_status.tweet_id:
            ids.add(block_status.tweet_id)
    return ids
