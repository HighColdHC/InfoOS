from __future__ import annotations


def classify_x_download_failure(stderr: str) -> str:
    lowered = stderr.lower()
    if (
        "browser_connect" in lowered
        or "profile not connected" in lowered
        or "profile disconnected" in lowered
        or ("browser profile" in lowered and "not connected" in lowered)
        or "exit code 69" in lowered
        or "exit status 69" in lowered
        or "exit 69" in lowered
    ):
        return "x_media_download_browser_session"
    if any(marker in lowered for marker in ("empty_result", "no media found", "returned no data", "no media files")):
        return "x_media_download_no_media_found"
    if any(marker in lowered for marker in ("auth_required", "login required", "not logged in", "unauthorized", "forbidden", "401", "403")):
        return "x_media_download_auth_required"
    return "x_media_download_failed"
