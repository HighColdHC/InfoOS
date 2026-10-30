"""Deliver derived thumbnails through the existing card-preview authorization boundary."""
from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse

from backend.api.routes.card_preview import _asset_is_deliverable, _error, _base_dir
from backend.infrastructure.media.thumbnails import THUMBNAILS_DIRNAME
from backend.infrastructure.storage.card_assets import CardAssetRepository
from backend.infrastructure.storage.card_asset_thumbnails import CardAssetThumbnailRepository
from backend.infrastructure.storage.paths import resolve_data_dir

router = APIRouter()


@router.api_route("/api/card-preview/assets/{asset_id}/thumbnail", methods=["GET", "HEAD"])
def api_card_preview_asset_thumbnail(request: Request, asset_id: str) -> Response:
    base_dir = _base_dir(request)
    asset = CardAssetRepository(base_dir).get_asset(asset_id)
    if asset is None or not _asset_is_deliverable(base_dir, asset):
        return _error("asset_not_found", "Asset not found", status_code=404)
    thumbnail = CardAssetThumbnailRepository(base_dir).get(asset_id)
    if thumbnail is None or thumbnail["status"] != "ready" or thumbnail["source_content_hash"] != asset["content_hash"]:
        return _error("thumbnail_not_found", "Thumbnail not found", status_code=404)
    assets_root = (resolve_data_dir(base_dir) / "assets").resolve()
    thumbnails_root = assets_root / THUMBNAILS_DIRNAME
    target = (assets_root / (thumbnail["local_path"] or "")).resolve()
    try:
        target.relative_to(thumbnails_root)
    except ValueError:
        return _error("asset_path_forbidden", "Thumbnail path is outside the managed thumbnails directory", status_code=403)
    if not target.is_file():
        return _error("thumbnail_file_not_found", "Thumbnail file not found", status_code=404)
    return FileResponse(target, media_type="image/webp",
                        headers={"Cache-Control": "private, max-age=604800"})
