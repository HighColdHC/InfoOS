"""Storage contracts for derived attachment thumbnails."""
from typing import Literal, TypedDict


class ThumbnailSourceRow(TypedDict):
    asset_id: str
    card_id: str
    media_kind: Literal["image", "video"]
    local_path: str
    content_hash: str | None


class CardAssetThumbnailRow(TypedDict):
    asset_id: str
    status: str
    local_path: str | None
    width: int | None
    height: int | None
    size_bytes: int | None
    source_content_hash: str | None
    error_code: str | None
    created_at: str
    updated_at: str


class ThumbnailSummary(TypedDict):
    pending_image: int
    pending_video: int
    ready: int
    failed: int
    ready_bytes: int
