from __future__ import annotations

import html
import re
from typing import TypedDict


class UserFacingContentBlock(TypedDict):
    block_kind: str
    status: str
    body: str


MACHINE_BLOCK_KINDS = {"llm_context", "enrichment_gap", "media_download_gap"}
READY_BLOCK_STATUSES = {"preserved", "downloaded", "enriched", "body_fetched", "done"}

_TAG_RE = re.compile(r"<[^>]+>")


def is_user_facing_content_block(block: UserFacingContentBlock) -> bool:
    kind = block["block_kind"]
    if kind in MACHINE_BLOCK_KINDS:
        return False
    status = block["status"]
    if status and status not in READY_BLOCK_STATUSES:
        return False
    body = block["body"].strip()
    if kind in {"image", "video"}:
        return bool(body)
    return bool(_clean_text(body))


def _clean_text(value: str) -> str:
    text = html.unescape(value)
    text = _TAG_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()
