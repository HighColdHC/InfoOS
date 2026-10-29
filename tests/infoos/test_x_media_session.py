from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.application.enrichment.media import x_session
from backend.infrastructure.storage.repositories import CardAssetRepository, CardRepository

MINIMAL_JPEG = b"\xff\xd8\xff\xe0image"


def _ready_prepare() -> dict:
    return {
        "status": "ready",
        "profile": {
            "alias": "infoos-fetch",
            "bound": True,
            "binding_status": "ready",
        },
    }


def _result(returncode: int, stderr: str = "", rows: list[dict] | None = None) -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=json.dumps(rows or []), stderr=stderr)


@contextmanager
def _unlocked():
    yield


def _patch_ready_lifecycle(monkeypatch, *, cleanup=None) -> None:
    monkeypatch.setattr(x_session, "prepare_opencli_browser_session", lambda **_kwargs: _ready_prepare())
    monkeypatch.setattr(x_session, "cleanup_headless_browser_session", cleanup or (lambda: None))
    monkeypatch.setattr(x_session, "opencli_browser_lock", _unlocked)


def test_x_download_command_uses_explicit_prepared_alias(monkeypatch, tmp_path):
    from backend.application.enrichment.media import download

    _patch_ready_lifecycle(monkeypatch)
    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card-x-profile",
            "source_platform": "x",
            "source_type": "social_post",
            "source_url": "https://x.com/example/status/1",
            "title": "X post",
            "content": "text",
            "captured_at": "2026-07-14T00:00:00Z",
            "processed_at": "2026-07-14T00:00:00Z",
            "raw_payload": {},
        }
    )
    commands: list[list[str]] = []

    def run(command, **_kwargs):
        commands.append(command)
        output_dir = Path(command[command.index("--output") + 1]) / "tweets"
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "tweet_1.jpg").write_bytes(MINIMAL_JPEG)
        return _result(0, rows=[{"index": 1, "tweet_id": "1", "url": "https://pbs.twimg.com/media/photo.jpg", "type": "image", "status": "success"}])

    monkeypatch.setattr(download.subprocess, "run", run)
    monkeypatch.setattr(
        download,
        "run_x_yt_dlp_fallback",
        lambda *_args, **_kwargs: pytest.fail("yt-dlp must not run after OpenCLI success"),
    )

    blocks = download.download_x_card_media(
        "card-x-profile",
        "https://x.com/example/status/1",
        tmp_path,
    )

    assert commands[0][1:5] == ["--profile", "infoos-fetch", "twitter", "download"]
    assert len(blocks) == 1
    asset = CardAssetRepository(tmp_path).list_for_card("card-x-profile")[0]
    assert asset["content_hash"].startswith("sha256:")
    assert asset["raw_payload"] == {"strategy": "opencli", "tweet_id": "1", "remote_url": "https://pbs.twimg.com/media/photo.jpg"}


def test_preflight_blocked_starts_no_download_and_cleans_up(monkeypatch, tmp_path):
    cleanups: list[str] = []
    commands: list[str] = []
    monkeypatch.setattr(
        x_session,
        "prepare_opencli_browser_session",
        lambda **_kwargs: {"status": "blocked", "profile": {}},
    )
    monkeypatch.setattr(x_session, "cleanup_headless_browser_session", lambda: cleanups.append("cleanup"))

    result = x_session.run_x_media_download_lifecycle(
        tweet_url="https://x.com/example/status/1",
        staging_root=tmp_path,
        command_runner=lambda _alias, _attempt_dir: commands.append("run"),
    )

    assert commands == []
    assert cleanups == ["cleanup"]
    assert result.failure_reason == "x_media_download_browser_session"
    assert result.details["download_attempt_count"] == 0


def test_prepare_exception_starts_no_download_and_cleans_up(monkeypatch, tmp_path):
    cleanups: list[str] = []
    monkeypatch.setattr(
        x_session,
        "prepare_opencli_browser_session",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("prepare failed")),
    )
    monkeypatch.setattr(x_session, "cleanup_headless_browser_session", lambda: cleanups.append("cleanup"))

    result = x_session.run_x_media_download_lifecycle(
        tweet_url="https://x.com/example/status/1",
        staging_root=tmp_path,
        command_runner=lambda _alias, _attempt_dir: pytest.fail("download must not start"),
    )

    assert result.failure_reason == "x_media_download_browser_session"
    assert result.details["preflight_outcome"] == "exception"
    assert cleanups == ["cleanup"]


@pytest.mark.parametrize("lock_error", [TimeoutError("busy"), RuntimeError("lock failed")])
def test_download_lock_failure_starts_no_subprocess_and_is_browser_session(monkeypatch, lock_error, tmp_path):
    _patch_ready_lifecycle(monkeypatch)

    @contextmanager
    def failed_lock():
        raise lock_error
        yield

    monkeypatch.setattr(x_session, "opencli_browser_lock", failed_lock)
    result = x_session.run_x_media_download_lifecycle(
        tweet_url="https://x.com/example/status/1",
        staging_root=tmp_path,
        command_runner=lambda _alias, _attempt_dir: pytest.fail("download must not start"),
    )

    assert result.failure_reason == "x_media_download_browser_session"
    assert result.details["download_attempt_count"] == 0


def test_session_failure_repairs_once_then_succeeds_and_cleans_each_prepare(monkeypatch, tmp_path):
    prepares: list[str] = []
    cleanups: list[str] = []
    commands = [_result(69, "BROWSER_CONNECT failed"), _result(0)]
    monkeypatch.setattr(
        x_session,
        "prepare_opencli_browser_session",
        lambda **_kwargs: prepares.append("prepare") or _ready_prepare(),
    )
    monkeypatch.setattr(x_session, "cleanup_headless_browser_session", lambda: cleanups.append("cleanup"))
    monkeypatch.setattr(x_session, "opencli_browser_lock", _unlocked)

    result = x_session.run_x_media_download_lifecycle(
        tweet_url="https://x.com/example/status/1",
        staging_root=tmp_path,
        command_runner=lambda _alias, _attempt_dir: commands.pop(0),
    )

    assert result.failure_reason is None
    assert prepares == ["prepare", "prepare"]
    assert cleanups == ["cleanup", "cleanup"]
    assert result.details["download_attempt_count"] == 2
    assert result.details["repair_outcome"] == "ready"


def test_batch_second_cycle_uses_shared_repair_instead_of_direct_prepare(monkeypatch, tmp_path):
    from backend.application.tasks.types import Task, TaskType
    from backend.application.workflows.browser_acquisition_batch import (
        BrowserBatchState,
        bind_browser_acquisition_batch,
    )

    task = Task("media_x", TaskType.MEDIA_DOWNLOAD, "card", "card_x")
    state = BrowserBatchState(
        tmp_path,
        [task],
        _ready_prepare(),
        False,
        ["card_x"],
    )
    repairs: list[dict] = []
    monkeypatch.setattr(
        x_session,
        "prepare_opencli_browser_session",
        lambda **_kwargs: pytest.fail("batch retry must use shared repair"),
    )
    monkeypatch.setattr(
        x_session,
        "repair_browser_acquisition_batch",
        lambda _state, **kwargs: repairs.append(kwargs) or _ready_prepare(),
    )
    monkeypatch.setattr(x_session, "opencli_browser_lock", _unlocked)
    commands = [_result(69, "BROWSER_CONNECT failed"), _result(0)]

    with bind_browser_acquisition_batch(state, task=task):
        result = x_session.run_x_media_download_lifecycle(
            tweet_url="https://x.com/example/status/1",
            staging_root=tmp_path,
            command_runner=lambda _alias, _attempt_dir: commands.pop(0),
        )

    assert result.failure_reason is None
    assert len(repairs) == 1
    assert repairs[0]["original_diagnostic"]["reason"] == "x_media_download_browser_session"
    assert result.details["prepare_attempt_count"] == 2


def test_repair_failure_stops_after_two_commands(monkeypatch, tmp_path):
    _patch_ready_lifecycle(monkeypatch)
    calls: list[str] = []

    result = x_session.run_x_media_download_lifecycle(
        tweet_url="https://x.com/example/status/1",
        staging_root=tmp_path,
        command_runner=lambda _alias, _attempt_dir: calls.append("run") or _result(69, "profile not connected"),
    )

    assert result.failure_reason == "x_media_download_browser_session"
    assert calls == ["run", "run"]
    assert result.details["prepare_attempt_count"] == 2
    assert result.details["download_attempt_count"] == 2
    assert "stderr" not in result.details
    assert result.details["stderr_summary"] == "[]\nprofile not connected"


def test_failed_download_diagnostic_is_bounded_and_redacted(monkeypatch, tmp_path):
    _patch_ready_lifecycle(monkeypatch)
    unsafe = (
        "Authorization: Bearer abcdefghijklmnop\n"
        "Cookie: sessionid=raw-cookie\n"
        "user@example.com +1 (212) 555-0199\n"
        "https://example.com/path?token=raw-token&next=private\n"
        + "x" * 1200
    )

    result = x_session.run_x_media_download_lifecycle(
        tweet_url="https://x.com/example/status/1",
        staging_root=tmp_path,
        command_runner=lambda _alias, _attempt_dir: _result(2, unsafe),
    )

    summary = result.details["stderr_summary"]
    assert result.failure_reason == "x_media_download_failed"
    assert len(summary) <= 800
    assert "raw-cookie" not in summary
    assert "raw-token" not in summary
    assert "user@example.com" not in summary
    assert "555-0199" not in summary
    assert "stderr" not in result.details


def test_non_session_failure_does_not_repair(monkeypatch, tmp_path):
    prepares: list[str] = []
    monkeypatch.setattr(
        x_session,
        "prepare_opencli_browser_session",
        lambda **_kwargs: prepares.append("prepare") or _ready_prepare(),
    )
    monkeypatch.setattr(x_session, "cleanup_headless_browser_session", lambda: None)
    monkeypatch.setattr(x_session, "opencli_browser_lock", _unlocked)

    result = x_session.run_x_media_download_lifecycle(
        tweet_url="https://x.com/example/status/1",
        staging_root=tmp_path,
        command_runner=lambda _alias, _attempt_dir: _result(1, "unknown download failure"),
    )

    assert result.failure_reason == "x_media_download_failed"
    assert prepares == ["prepare"]
    assert result.details["download_attempt_count"] == 1
    assert result.details["repair_outcome"] == "not_attempted"


def test_prepare_download_and_cleanup_locks_are_not_nested(monkeypatch, tmp_path):
    depth = 0
    max_depth = 0

    @contextmanager
    def tracked_lock():
        nonlocal depth, max_depth
        depth += 1
        max_depth = max(max_depth, depth)
        try:
            yield
        finally:
            depth -= 1

    def prepare(**_kwargs):
        with tracked_lock():
            return _ready_prepare()

    def cleanup():
        with tracked_lock():
            return None

    monkeypatch.setattr(x_session, "prepare_opencli_browser_session", prepare)
    monkeypatch.setattr(x_session, "cleanup_headless_browser_session", cleanup)
    monkeypatch.setattr(x_session, "opencli_browser_lock", tracked_lock)

    result = x_session.run_x_media_download_lifecycle(
        tweet_url="https://x.com/example/status/1",
        staging_root=tmp_path,
        command_runner=lambda _alias, _attempt_dir: _result(0),
    )

    assert result.failure_reason is None
    assert max_depth == 1


def test_repair_attempts_use_isolated_staging_and_commit_only_success(monkeypatch, tmp_path):
    from backend.application.enrichment.media import download

    _patch_ready_lifecycle(monkeypatch)
    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card-x-repair-stage",
            "source_platform": "x",
            "source_type": "social_post",
            "source_url": "https://x.com/example/status/123",
            "title": "post",
            "content": "text",
            "captured_at": "2026-07-14T00:00:00Z",
            "processed_at": "2026-07-14T00:00:00Z",
            "raw_payload": {},
        }
    )
    attempt_dirs: list[Path] = []

    def run(command, **_kwargs):
        attempt = Path(command[command.index("--output") + 1])
        attempt_dirs.append(attempt)
        assert attempt.stat().st_mode & 0o777 == 0o700
        tweets = attempt / "tweets"
        tweets.mkdir()
        payload = MINIMAL_JPEG + (b"first" if len(attempt_dirs) == 1 else b"second")
        (tweets / "tweet_1.jpg").write_bytes(payload)
        return _result(69, "BROWSER_CONNECT failed") if len(attempt_dirs) == 1 else _result(0, rows=[{"index": 1, "tweet_id": "123", "url": "https://pbs.twimg.com/media/photo.jpg", "type": "image", "status": "success"}])

    monkeypatch.setattr(download.subprocess, "run", run)
    monkeypatch.setattr(
        download,
        "run_x_yt_dlp_fallback",
        lambda *_args, **_kwargs: pytest.fail("fallback must not run after repaired success"),
    )
    blocks = download.download_x_card_media(
        "card-x-repair-stage", "https://x.com/example/status/123", tmp_path
    )

    assert len(blocks) == 1
    assert len(attempt_dirs) == 2 and attempt_dirs[0] != attempt_dirs[1]
    assert all(not path.exists() for path in attempt_dirs)
    durable = list((tmp_path / "data" / "assets" / "card-x-repair-stage" / "tweets").glob("*.jpg"))
    assert len(durable) == 1 and durable[0].read_bytes().endswith(b"second")
    assert durable[0].stat().st_mode & 0o777 == 0o600
    asset = CardAssetRepository(tmp_path).list_for_card("card-x-repair-stage")[0]
    assert asset["raw_payload"] == {"strategy": "opencli", "tweet_id": "123", "remote_url": "https://pbs.twimg.com/media/photo.jpg"}


def test_failed_attempt_media_is_cleaned_before_fallback_and_never_durable(monkeypatch, tmp_path):
    from backend.application.enrichment.media import download

    _patch_ready_lifecycle(monkeypatch)
    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card-x-failed-stage",
            "source_platform": "x",
            "source_type": "social_post",
            "source_url": "https://x.com/example/status/123",
            "title": "post",
            "content": "text",
            "captured_at": "2026-07-14T00:00:00Z",
            "processed_at": "2026-07-14T00:00:00Z",
            "raw_payload": {},
        }
    )
    attempt_dirs: list[Path] = []

    def run(command, **_kwargs):
        attempt = Path(command[command.index("--output") + 1])
        attempt_dirs.append(attempt)
        tweets = attempt / "tweets"
        tweets.mkdir()
        (tweets / "complete.jpg").write_bytes(MINIMAL_JPEG + b"complete")
        return _result(69, "profile not connected")

    monkeypatch.setattr(download.subprocess, "run", run)
    monkeypatch.setattr(download, "run_x_yt_dlp_fallback", lambda *_args, **_kwargs: SimpleNamespace(
        status="failed", files=[], provenance={}, evidence={"reason": "fallback_failed"}
    ))
    assert download.download_x_card_media(
        "card-x-failed-stage", "https://x.com/example/status/123", tmp_path
    ) == []
    assert len(attempt_dirs) == 2 and all(not path.exists() for path in attempt_dirs)
    durable = tmp_path / "data" / "assets" / "card-x-failed-stage" / "tweets"
    assert not durable.exists() or list(durable.iterdir()) == []
    assert CardAssetRepository(tmp_path).list_for_card("card-x-failed-stage") == []
