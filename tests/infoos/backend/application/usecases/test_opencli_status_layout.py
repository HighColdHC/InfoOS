from backend.application.usecases import opencli_status
from backend.infrastructure.runtime import media_tools


def test_opencli_status_includes_passive_media_tools(monkeypatch):
    monkeypatch.setattr(
        media_tools.shutil,
        "which",
        lambda name: "/usr/local/bin/" + name if name == "yt-dlp" else None,
    )

    payload = opencli_status._media_tool_status()

    assert payload["yt_dlp"]["status"] == "installed"
    assert payload["gallery_dl"]["status"] == "missing"
    assert payload["ffmpeg"]["status"] == "missing"
