"""Read-only HTTP routes for finalized information-card previews."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from backend.api.routes.card_preview_dates import publication_bounds
from backend.api.asset_delivery import (
    file_range,
    parse_single_range,
    safe_media_type,
)
from backend.application.usecases.manual_capture import read_manual_capture

from backend.infrastructure.storage.paths import resolve_data_dir
from backend.infrastructure.storage.repositories import CardAssetRepository, CardRepository
from backend.read_models.card_browse import read_card_preview_facets
from backend.read_models.card_preview import read_card_preview_detail, read_card_preview_page


router = APIRouter()


def _base_dir(request: Request) -> Path:
    return Path(request.app.state.base_dir)


@router.get("/api/card-preview")
def api_card_preview_page(
    request: Request,
    query: str | None = None,
    platform: str | None = None,
    source_id: str | None = None,
    published_since: str | None = None,
    published_until: str | None = None,
    evaluation: str | None = None,
    completeness: str | None = None,
    status: str = "active",
    sort: str = "published_desc",
    recommended: str | None = None,
    digest: str | None = None,
    limit: str = "25",
    offset: str = "0",
) -> Any:
    try:
        parsed_limit = int(limit)
    except (TypeError, ValueError):
        parsed_limit = 0
    if parsed_limit < 1 or parsed_limit > 100:
        return _error(
            "invalid_pagination",
            "limit must be an integer between 1 and 100",
            status_code=422,
        )
    try:
        parsed_offset = int(offset)
    except (TypeError, ValueError):
        parsed_offset = -1
    if parsed_offset < 0:
        return _error(
            "invalid_pagination",
            "offset must be a non-negative integer",
            status_code=422,
        )
    normalized_completeness = (completeness or "").strip()
    if normalized_completeness and normalized_completeness not in {"complete", "partial"}:
        return _error(
            "invalid_completeness",
            "completeness must be complete or partial",
            status_code=422,
        )
    normalized_status = status.strip()
    if normalized_status not in {"active", "processing"}:
        return _error(
            "invalid_status",
            "status must be active or processing",
            status_code=422,
        )
    normalized_recommended = (recommended or "").strip()
    if normalized_recommended not in {"", "1"}:
        return _error("invalid_recommended", "recommended must be 1 or empty", status_code=422)
    normalized_evaluation = (evaluation or "").strip()
    if normalized_evaluation not in {"", "passed", "evaluated", "unevaluated"}:
        return _error("invalid_evaluation", "evaluation 须为 passed、evaluated 或 unevaluated", status_code=422)
    if normalized_recommended == "1":
        if normalized_evaluation not in {"", "passed"}:
            return _error("invalid_evaluation", "recommended=1 只能与 passed 视图一起使用", status_code=422)
        normalized_evaluation = "passed"
    if sort not in {"published_desc", "updated_desc", "relevance_desc"} or (
        sort == "relevance_desc" and normalized_evaluation != "passed"
    ):
        message = "relevance_desc 仅支持 passed 视图（或 recommended=1）" if sort == "relevance_desc" else "sort must be published_desc or updated_desc"
        return _error("invalid_sort", message, status_code=422)
    if digest is not None and digest not in {"0", "1"}:
        return _error("invalid_digest", "digest must be 0 or 1", status_code=422)
    try:
        since, until = publication_bounds(published_since, published_until)
    except ValueError as exc:
        return _error("invalid_published_range", str(exc), status_code=422)
    return read_card_preview_page(
        _base_dir(request),
        query=query,
        platform=platform,
        source_id=source_id,
        published_since=since, published_until=until,
        evaluation=normalized_evaluation or None,
        completeness=normalized_completeness or None,
        status=normalized_status,
        sort=sort,
        recommended=normalized_recommended == "1",
        digest=None if digest is None else digest == "1",
        limit=parsed_limit,
        offset=parsed_offset,
    )


@router.get("/api/card-preview/facets")
def api_card_preview_facets(
    request: Request, published_since: str | None = None,
    published_until: str | None = None, scope: str = "all",
) -> Any:
    if scope not in {"all", "maintained"}:
        return _error("invalid_scope", "scope must be all or maintained", status_code=422)
    try:
        since, until = publication_bounds(published_since, published_until)
    except ValueError as exc:
        return _error("invalid_published_range", str(exc), status_code=422)
    return read_card_preview_facets(_base_dir(request), published_since=since,
                                    published_until=until, scope=scope)


@router.api_route("/api/card-preview/assets/{asset_id}", methods=["GET", "HEAD"])
def api_card_preview_asset(
    request: Request,
    asset_id: str,
    range_header: str | None = Header(default=None, alias="Range"),
) -> Response:
    base_dir = _base_dir(request)
    asset = CardAssetRepository(base_dir).get_asset(asset_id)
    if asset is None or not _asset_is_deliverable(base_dir, asset):
        return _error("asset_not_found", "Asset not found", status_code=404)

    assets_root = (resolve_data_dir(base_dir) / "assets").resolve()
    target = (assets_root / (asset["local_path"] or "")).resolve()
    try:
        target.relative_to(assets_root)
    except ValueError:
        return _error(
            "asset_path_forbidden",
            "Asset path is outside the managed assets directory",
            status_code=403,
        )
    if not target.is_file():
        return _error("asset_file_not_found", "Asset file not found", status_code=404)
    size = target.stat().st_size
    media_type = safe_media_type(target, asset["mime_type"])
    if not range_header or request.method == "HEAD":
        return FileResponse(
            target,
            media_type=media_type,
            headers={"Accept-Ranges": "bytes"},
        )
    try:
        start, end = parse_single_range(range_header, size)
    except ValueError:
        return _error(
            "range_not_satisfiable",
            "Requested byte range is not satisfiable",
            status_code=416,
            headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"},
        )
    length = end - start + 1
    return StreamingResponse(
        file_range(target, start, length),
        status_code=206,
        media_type=media_type,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Content-Length": str(length),
        },
    )


@router.get("/api/card-preview/captures/{capture_id}")
def api_card_preview_capture(request: Request, capture_id: str) -> JSONResponse:
    receipt = read_manual_capture(_base_dir(request), capture_id=capture_id)
    if receipt is None:
        return _error("capture_not_found", "Capture not found", status_code=404)
    return JSONResponse(
        {
            "capture_id": capture_id,
            "processing_status": receipt["processing_status"],
            "card_id": receipt["card_id"],
            "infoos_url": receipt["infoos_url"],
            "error_code": receipt["error_code"],
            "updated_at": receipt["updated_at"],
        }
    )


@router.api_route("/api/card-preview/{card_id}", methods=["GET", "HEAD"])
def api_card_preview_detail(request: Request, card_id: str) -> JSONResponse:
    detail = read_card_preview_detail(_base_dir(request), card_id)
    if detail is None:
        return _error("card_not_found", "Card not found", status_code=404)
    return JSONResponse(detail)


def _asset_is_deliverable(base_dir: Path, asset: dict[str, Any]) -> bool:
    card = CardRepository(base_dir).get_card(asset["card_id"])
    return (
        card is not None
        and card["status"] == "active"
        and card["version"] >= 1
        and bool((card["content_hash"] or "").strip())
        and asset["status"] in {"done", "downloaded", "enriched", "preserved", "ready", "understood"}
        and asset["cleanup_state"] == "present"
    )


def _error(
    code: str,
    message: str,
    *,
    status_code: int,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        {"code": code, "message": message},
        status_code=status_code,
        headers=headers,
    )
