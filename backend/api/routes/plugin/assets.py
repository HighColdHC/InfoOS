"""Safe plugin attachment delivery with single HTTP byte ranges."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from backend.api.routes.plugin.dependencies import (
    error_response,
    require_assets_read,
    set_audit_meta,
)
from backend.api.asset_delivery import (
    file_range,
    parse_single_range,
    safe_media_type,
)
from backend.read_models.plugin_cards import resolve_plugin_asset


router = APIRouter()


@router.get("/assets/{asset_id}")
def asset_file(
    request: Request,
    asset_id: str,
    range_header: str | None = Header(default=None, alias="Range"),
    auth: Any = Depends(require_assets_read),
) -> Any:
    if isinstance(auth, JSONResponse):
        return auth
    resolved = resolve_plugin_asset(Path(request.app.state.base_dir), asset_id)
    if resolved is None:
        return error_response(request, "asset_not_found", "Asset not found", status_code=404)
    path = resolved["path"]
    size = int(resolved["size_bytes"])
    media_type = safe_media_type(path, resolved["mime_type"])
    if not range_header:
        set_audit_meta(request, asset_id=asset_id, response_bytes=size)
        return FileResponse(
            path,
            media_type=media_type,
            headers={"Accept-Ranges": "bytes"},
        )
    try:
        start, end = parse_single_range(range_header, size)
    except ValueError:
        return error_response(
            request,
            "range_not_satisfiable",
            "Requested byte range is not satisfiable",
            status_code=416,
            headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"},
        )
    length = end - start + 1
    range_value = f"bytes {start}-{end}/{size}"
    set_audit_meta(
        request,
        asset_id=asset_id,
        response_bytes=length,
        range=range_value,
    )
    return StreamingResponse(
        file_range(path, start, length),
        status_code=206,
        media_type=media_type,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Range": range_value,
            "Content-Length": str(length),
        },
    )
