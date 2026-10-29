"""Local card asset HTTP route."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from backend.api.asset_delivery import safe_media_type
from backend.infrastructure.storage.paths import resolve_data_dir

router = APIRouter()


def _base_dir(request: Request) -> Path:
    return Path(request.app.state.base_dir)


@router.get("/api/assets/{file_path:path}")
def api_assets(request: Request, file_path: str) -> Response:
    base_dir = _base_dir(request)

    if ".." in file_path.split("/"):
        return JSONResponse({"error": "Invalid path"}, status_code=403)
    assets_dir = (resolve_data_dir(base_dir) / "assets").resolve()
    target = (assets_dir / file_path).resolve()
    try:
        target.relative_to(assets_dir)
    except ValueError:
        return JSONResponse({"error": "Invalid path"}, status_code=403)
    if not target.exists():
        return JSONResponse({"error": "Not found"}, status_code=404)
    return FileResponse(
        target,
        media_type=safe_media_type(target, None),
        headers={"X-Content-Type-Options": "nosniff"},
    )
