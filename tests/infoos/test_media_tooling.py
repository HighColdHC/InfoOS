from pathlib import Path
from types import SimpleNamespace

from backend.application.enrichment.media.tooling import (
    _resolve_media_tool,
    run_gallery_dl_download,
    run_yt_dlp_download,
)

MINIMAL_MP4 = b"\x00\x00\x00\x18ftypmp42video"
MINIMAL_JPEG = b"\xff\xd8\xff\xe0image"


def test_yt_dlp_missing_returns_blocked(monkeypatch, tmp_path):
    monkeypatch.setattr("backend.application.enrichment.media.tooling._resolve_media_tool", lambda name: None)

    result = run_yt_dlp_download("https://www.youtube.com/watch?v=abc", tmp_path)

    assert result.status == "blocked"
    assert result.reason == "yt_dlp_missing"
    assert result.files == []


def test_yt_dlp_success_lists_downloaded_files(monkeypatch, tmp_path):
    monkeypatch.setattr("backend.application.enrichment.media.tooling._resolve_media_tool", lambda name: "/usr/local/bin/yt-dlp")

    def fake_run(command, **kwargs):
        assert "--ignore-config" in command
        assert "--no-playlist" in command
        assert "--write-info-json" in command
        assert "--socket-timeout" in command
        assert "--retries" in command
        assert "--fragment-retries" in command
        assert "--max-filesize" in command
        assert "--cookies-from-browser" not in command
        assert command[command.index("--js-runtimes") + 1] == "node"
        assert command[command.index("--remote-components") + 1] == "ejs:npm"
        assert "--write-subs" not in command
        assert "--write-auto-subs" not in command
        assert "--sub-langs" not in command
        output_dir = Path(command[command.index("--paths") + 1])
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "video.mp4").write_bytes(MINIMAL_MP4)
        (output_dir / "thumb.jpg").write_bytes(MINIMAL_JPEG)
        return SimpleNamespace(returncode=0, stdout="/tmp/video.mp4\n", stderr="")

    monkeypatch.setattr("backend.application.enrichment.media.tooling.subprocess.run", fake_run)

    result = run_yt_dlp_download("https://www.youtube.com/watch?v=abc", tmp_path)

    assert result.status == "downloaded"
    assert {file.kind for file in result.files} == {"image", "video"}


def test_yt_dlp_uses_explicit_browser_cookie_source(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "backend.application.enrichment.media.tooling._resolve_media_tool",
        lambda name: "/usr/local/bin/yt-dlp",
    )

    def fake_run(command, **kwargs):
        assert command[command.index("--cookies-from-browser") + 1] == "chrome:/tmp/infoos-profile"
        output_dir = Path(command[command.index("--paths") + 1])
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "video.mp4").write_bytes(MINIMAL_MP4)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("backend.application.enrichment.media.tooling.subprocess.run", fake_run)

    result = run_yt_dlp_download(
        "https://www.youtube.com/watch?v=abc",
        tmp_path,
        cookies_from_browser="chrome:/tmp/infoos-profile",
    )

    assert result.status == "downloaded"


def test_gallery_dl_failure_classifies_auth_required(monkeypatch, tmp_path):
    monkeypatch.setattr("backend.application.enrichment.media.tooling._resolve_media_tool", lambda name: "/usr/local/bin/gallery-dl")
    monkeypatch.setattr(
        "backend.application.enrichment.media.tooling.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr="HTTP 401 Unauthorized"),
    )

    result = run_gallery_dl_download("https://www.reddit.com/gallery/example", tmp_path)

    assert result.status == "blocked"
    assert result.reason == "auth_required"


def test_gallery_dl_network_security_block_classifies_auth_required(monkeypatch, tmp_path):
    monkeypatch.setattr("backend.application.enrichment.media.tooling._resolve_media_tool", lambda name: "/usr/local/bin/gallery-dl")
    monkeypatch.setattr(
        "backend.application.enrichment.media.tooling.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="You've been blocked by network security. To continue, log in to your Reddit account.",
        ),
    )

    result = run_gallery_dl_download("https://www.reddit.com/gallery/example", tmp_path)

    assert result.status == "blocked"
    assert result.reason == "auth_required"


def test_media_tool_resolves_venv_local_executable(monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_python = bin_dir / "python"
    fake_python.write_text("")
    fake_tool = bin_dir / "yt-dlp"
    fake_tool.write_text("")
    monkeypatch.setattr("backend.application.enrichment.media.tooling.shutil.which", lambda name: None)
    monkeypatch.setattr("backend.application.enrichment.media.tooling.sys.executable", str(fake_python))

    assert _resolve_media_tool("yt-dlp") == str(fake_tool)
