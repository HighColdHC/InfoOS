"""FETCH acquisition workflow contracts: persist raw inputs, never cards."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.application.tasks.planner import raw_item_target_id
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskType
from backend.domain.models.raw_item import RawItem
from backend.infrastructure.connectors.base import FetchResult
from backend.infrastructure.storage.repositories import RawItemRepository, RunRepository, SourceCursorRepository, SourceRepository, TaskEventRepository
from tests.infoos._factories import make_source_payload, make_x_source_payload, card_count


def _task() -> Task:
    return Task(
        task_id="fetch_rss_acquisition_20260710_080000",
        task_type=TaskType.FETCH,
        target_kind="source",
        target_id="rss_acquisition",
        artifacts_summary={
            "chain_id": "source_run_rss_acquisition_20260710_080000",
            "chain_root_task_id": "fetch_rss_acquisition_20260710_080000",
            "scheduled_for": "2026-07-10T08:00:00Z",
        },
    )


def _seed_source(base_dir: Path) -> None:
    source = make_source_payload(
            source_id="rss_acquisition",
            connector_id="rss_acquisition",
            identifier="https://example.com/acquisition.xml",
        )
    source["connector_status"] = "supported"
    SourceRepository(base_dir).upsert_source(source)


def _item(raw_item_id: str = "raw_acquisition_1") -> RawItem:
    return RawItem(
        raw_item_id=raw_item_id,
        connector_id="rss_acquisition",
        source_platform="rss",
        source_type="article",
        source_url=f"https://example.com/{raw_item_id}",
        captured_at="2026-07-10T08:00:00Z",
        raw_text="Fetched raw item that must wait for a separate card build task.",
        raw_metadata={"title": "Fetch-only acquisition"},
    )


class _Connector:
    connector_id = "rss_acquisition"

    def __init__(self, result: FetchResult):
        self.result = result
        self.cursors: list[str | None] = []

    def fetch_new_items(self, since_cursor: str | None) -> FetchResult:
        self.cursors.append(since_cursor)
        return self.result


class _EvidenceConnector(_Connector):
    discovery_evidence = {
        "executor": "opencli",
        "session_alias": "xiaohongshu-main",
        "sanitized_arguments": ["xiaohongshu", "user-notes", "[REDACTED]"],
        "links": [f"https://www.xiaohongshu.com/explore/link-{index}" for index in range(10)],
        "discovered_link_count": 10,
        "availability": "available",
    }


class _FailingConnector:
    connector_id = "rss_acquisition"

    def fetch_new_items(self, since_cursor: str | None) -> FetchResult:
        raise RuntimeError("login required for connector")


class _RepairableBrowserConnector:
    connector_id = "opencli_x_repair"

    def __init__(self) -> None:
        self.calls = 0

    def fetch_new_items(self, since_cursor: str | None) -> FetchResult:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("extension disconnected")
        return FetchResult(items=[], next_cursor="cursor-after")


def _workflow():
    from backend.application.workflows.acquisition import fetch_source

    return fetch_source


def test_fetch_handler_reads_sqlite_cursor(tmp_path: Path, monkeypatch) -> None:
    _seed_source(tmp_path)
    SourceCursorRepository(tmp_path).upsert(
        source_id="rss_acquisition",
        connector_id="rss_acquisition",
        cursor_value="cursor-before",
        last_run_id="prior-run",
    )
    connector = _Connector(FetchResult(items=[], next_cursor="cursor-after"))
    monkeypatch.setattr("backend.application.workflows.acquisition._build_connector", lambda base_dir, source: connector)

    artifacts = _workflow()(tmp_path, source_id="rss_acquisition", task=_task())

    assert connector.cursors == ["cursor-before"]
    assert artifacts["cursor_before"] == "cursor-before"
    assert artifacts["cursor_after"] == "cursor-after"


@pytest.mark.parametrize("reinitialized", [True, False])
def test_douyin_creator_skips_existing_pinned_raw_item_in_both_cursor_paths(tmp_path: Path, monkeypatch, reinitialized) -> None:
    source = make_source_payload(
        source_id="douyin_creator_cursor", connector_id="douyin_creator_cursor",
        platform="douyin", source_type="creator", identifier="sec_uid",
    )
    source["connector_status"] = "supported"
    SourceRepository(tmp_path).upsert_source(source)
    old_cursor = "https://www.douyin.com/video/100"
    SourceCursorRepository(tmp_path).upsert(
        source_id=source["source_id"], connector_id=source["connector_id"],
        cursor_value=old_cursor, last_run_id="prior-run",
    )

    def video(id: str) -> RawItem:
        return RawItem(
            raw_item_id=f"https://www.douyin.com/video/{id}",
            connector_id=source["connector_id"], source_platform="douyin", source_type="creator",
            source_url=f"https://www.douyin.com/video/{id}", captured_at="2026-09-27T00:00:00Z",
            raw_text=id,
            raw_metadata={"opencli_record": {"is_top": id == "110"}},
        )

    RawItemRepository(tmp_path).upsert_raw_items([video("110")], run_id="prior-run")

    class ReinitializedConnector:
        cursor_reinitialized = reinitialized
        discovery_evidence = {
            "old_cursor_id_count": 1, "pinned_item_count": 1, "availability": "available",
            "discovered_link_count": 2,
        }

        def fetch_new_items(self, since_cursor):
            assert since_cursor == old_cursor
            return FetchResult(
                items=[video("111"), video("110")],
                next_cursor='douyin-creator-v1:["111","110"]', visible_count=2,
            )

    monkeypatch.setattr("backend.application.workflows.acquisition._build_connector", lambda *_: ReinitializedConnector())
    task = Task(
        task_id="fetch_douyin_cursor", task_type=TaskType.FETCH, target_kind="source",
        target_id=source["source_id"],
    )
    artifacts = _workflow()(tmp_path, source_id=source["source_id"], task=task)

    assert artifacts["raw_item_ids"] == [video("111").raw_item_id]
    if reinitialized:
        assert artifacts["douyin_cursor_reinitialized"] == {"old_cursor_id_count": 1}
    else:
        assert "douyin_cursor_reinitialized" not in artifacts
    assert artifacts["dedup_skipped_count"] == 1
    assert artifacts["discovery_evidence"]["pinned_item_count"] == 1
    assert len(artifacts["card_build_task_ids"]) == 1
    assert TaskRepository.for_base_dir(tmp_path).get(artifacts["card_build_task_ids"][0]).target_id == raw_item_target_id(
        source["connector_id"], video("111").raw_item_id,
    )
    assert SourceCursorRepository(tmp_path).get(source["source_id"])["cursor_value"] == 'douyin-creator-v1:["111","110"]'
    run = RunRepository(tmp_path).get_run(task.task_id)
    if reinitialized:
        assert run["raw_payload"]["douyin_cursor_reinitialized"] == {"old_cursor_id_count": 1}
    else:
        assert "douyin_cursor_reinitialized" not in run["raw_payload"]


def test_fetch_handler_commits_raw_items_and_card_build_tasks(tmp_path: Path, monkeypatch) -> None:
    _seed_source(tmp_path)
    connector = _Connector(FetchResult(items=[_item()], next_cursor="cursor-after", visible_count=3, dedup_skipped_count=2))
    monkeypatch.setattr("backend.application.workflows.acquisition._build_connector", lambda base_dir, source: connector)

    artifacts = _workflow()(tmp_path, source_id="rss_acquisition", task=_task())

    assert artifacts["raw_item_ids"] == ["raw_acquisition_1"]
    assert len(artifacts["card_build_task_ids"]) == 1
    card_build_task_id = artifacts["card_build_task_ids"][0]
    assert RawItemRepository(tmp_path).get_model("raw_acquisition_1") is not None
    card_build = TaskRepository.for_base_dir(tmp_path).get(card_build_task_id)
    assert card_build is not None
    assert card_build.target_id == raw_item_target_id("rss_acquisition", "raw_acquisition_1")
    assert SourceCursorRepository(tmp_path).get("rss_acquisition")["cursor_value"] == "cursor-after"
    event = TaskEventRepository(tmp_path).list_events(task_id="fetch_rss_acquisition_20260710_080000")[-1]
    assert event["event_type"] == "acquisition_committed"
    assert event["chain_id"] == "source_run_rss_acquisition_20260710_080000"
    assert event["payload"]["source_ids"] == ["rss_acquisition"]
    assert event["payload"]["run_id"] == "fetch_rss_acquisition_20260710_080000"


def test_fetch_handler_does_not_build_cards(tmp_path: Path, monkeypatch) -> None:
    _seed_source(tmp_path)
    connector = _Connector(FetchResult(items=[_item()], next_cursor="cursor-after"))
    monkeypatch.setattr("backend.application.workflows.acquisition._build_connector", lambda base_dir, source: connector)

    _workflow()(tmp_path, source_id="rss_acquisition", task=_task())

    assert card_count(tmp_path) == 0


def test_fetch_handler_returns_visible_and_dedup_counts(tmp_path: Path, monkeypatch) -> None:
    _seed_source(tmp_path)
    connector = _Connector(FetchResult(items=[_item()], next_cursor="cursor-after", visible_count=5, dedup_skipped_count=4))
    monkeypatch.setattr("backend.application.workflows.acquisition._build_connector", lambda base_dir, source: connector)

    artifacts = _workflow()(tmp_path, source_id="rss_acquisition", task=_task())

    assert artifacts["visible_count"] == 5
    assert artifacts["fetched_count"] == 1
    assert artifacts["dedup_skipped_count"] == 4


def test_fetch_handler_commits_explicit_discovery_evidence_with_raw_items(tmp_path: Path, monkeypatch) -> None:
    _seed_source(tmp_path)
    items = [_item(f"raw_acquisition_{index}") for index in range(7)]
    connector = _EvidenceConnector(
        FetchResult(items=items, next_cursor="cursor-after", visible_count=10, dedup_skipped_count=3)
    )
    monkeypatch.setattr(
        "backend.application.workflows.acquisition._build_connector",
        lambda base_dir, source: connector,
    )

    artifacts = _workflow()(tmp_path, source_id="rss_acquisition", task=_task())

    assert artifacts["discovered_link_count"] == 10
    assert artifacts["dedup_skipped_count"] == 3
    assert artifacts["raw_item_count"] == 7
    assert len(artifacts["discovered_links"]) == 10
    assert artifacts["discovery_availability"] == "available"
    assert artifacts["discovery_evidence"] == {
        "executor": "opencli",
        "session_alias": "xiaohongshu-main",
        "sanitized_arguments": ["xiaohongshu", "user-notes", "[REDACTED]"],
        "availability": "available",
    }


def test_fetch_handler_updates_sqlite_source_schedule_after_durable_acquisition(tmp_path: Path, monkeypatch) -> None:
    _seed_source(tmp_path)
    connector = _Connector(FetchResult(items=[], next_cursor="cursor-after"))
    monkeypatch.setattr("backend.application.workflows.acquisition._build_connector", lambda base_dir, source: connector)
    monkeypatch.setattr("backend.application.workflows.acquisition.utc_now_seconds_z", lambda: "2026-07-10T08:00:00Z")

    _workflow()(tmp_path, source_id="rss_acquisition", task=_task())

    source = SourceRepository(tmp_path).get_source("rss_acquisition")
    assert source["last_run_at"] == "2026-07-10T08:00:00Z"
    assert source["next_run_at"] == "2026-07-11T08:00:00Z"


def test_fetch_handler_replay_after_post_fetch_failure_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    _seed_source(tmp_path)
    connector = _Connector(FetchResult(items=[_item()], next_cursor="cursor-after"))
    monkeypatch.setattr("backend.application.workflows.acquisition._build_connector", lambda base_dir, source: connector)
    from backend.infrastructure.storage.runs import RunRepository

    original_append = RunRepository.append_to_connection
    calls = 0

    def fail_once(conn, payload):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("run history temporarily unavailable")
        return original_append(conn, payload)

    monkeypatch.setattr(RunRepository, "append_to_connection", fail_once)
    with pytest.raises(RuntimeError, match="run history temporarily unavailable"):
        _workflow()(tmp_path, source_id="rss_acquisition", task=_task())

    artifacts = _workflow()(tmp_path, source_id="rss_acquisition", task=_task())

    assert artifacts["raw_item_ids"] == ["raw_acquisition_1"]
    assert len(RawItemRepository(tmp_path).list_raw_items()) == 1
    assert len(artifacts["card_build_task_ids"]) == 1
    assert TaskRepository.for_base_dir(tmp_path).get(artifacts["card_build_task_ids"][0]) is not None


def test_empty_browser_result_with_render_failure_is_not_success() -> None:
    from backend.application.workflows.acquisition_failures import classify_acquisition_failure

    failure = classify_acquisition_failure(
        source={"platform": "douyin"},
        result={"items": [], "errors": [{"message": "did not render result cards"}]},
        error=None,
    )

    assert failure is not None
    assert failure.reason.value == "browser_session_render_blocked"
    assert failure.retryable is True


def test_douyin_render_failure_from_connector_config_uses_browser_repair_route() -> None:
    """Projected connector configs carry ``source_platform``, not ``platform``."""
    from backend.application.workflows.acquisition_failures import classify_acquisition_failure

    failure = classify_acquisition_failure(
        source={"source_platform": "douyin", "source_type": "social_post"},
        result={"items": [], "errors": [{"message": "Douyin search did not render result cards"}]},
        error=None,
    )

    assert failure is not None
    assert failure.diagnostic["category"] == "browser_session"
    assert failure.reason.value == "browser_session_render_blocked"
    assert failure.reason.value != "opencli_command_failed"


def test_parse_failure_is_blocked_with_actionable_reason() -> None:
    from backend.application.workflows.acquisition_failures import classify_acquisition_failure

    failure = classify_acquisition_failure(
        source={"platform": "rss"},
        result={"errors": [{"message": "invalid JSON", "raw_response_ref": "raw://response/1"}]},
        error=None,
    )

    assert failure is not None
    assert failure.reason.value == "parse_failed"
    assert failure.retryable is False
    assert failure.user_action == "Inspect connector output mapping before retrying."
    assert failure.diagnostic["raw_response_ref"] == "raw://response/1"


def test_chrome_not_configured_is_blocked_without_retry() -> None:
    from backend.application.workflows.acquisition_failures import classify_acquisition_failure

    failure = classify_acquisition_failure(
        source={"platform": "x"},
        result={"errors": [{"message": "chrome not configured: browser-session.json missing"}]},
        error=None,
    )

    assert failure is not None
    assert failure.reason.value == "chrome_not_configured"
    assert failure.retryable is False


def test_connector_failure_reaches_task_retry_boundary_with_diagnostic(tmp_path: Path, monkeypatch) -> None:
    _seed_source(tmp_path)
    monkeypatch.setattr(
        "backend.application.workflows.acquisition._build_connector",
        lambda base_dir, source: _FailingConnector(),
    )
    from backend.application.workflows.acquisition_failures import AcquisitionFailureError

    with pytest.raises(AcquisitionFailureError) as raised:
        _workflow()(tmp_path, source_id="rss_acquisition", task=_task())

    assert raised.value.failure.reason.value == "platform_not_logged_in"
    assert raised.value.failure.diagnostic["category"] == "auth_required"


def test_opencli_failure_preserves_only_safe_stderr_summary() -> None:
    from backend.application.workflows.acquisition_failures import classify_acquisition_failure
    from backend.infrastructure.connectors.opencli import OpenCLICommandError

    error = OpenCLICommandError(
        1,
        stderr_summary="login required token=raw-token https://example.com/?account=private",
    )
    failure = classify_acquisition_failure(
        source={"platform": "douyin", "source_type": "creator"},
        result=None,
        error=error,
    )

    assert failure is not None
    assert failure.reason.value == "platform_not_logged_in"
    assert failure.diagnostic["stderr_summary"] == (
        "login required token=[REDACTED] https://example.com/?account=[REDACTED]"
    )
    assert "raw-token" not in failure.message
    assert "stderr" not in failure.diagnostic
    assert "stdout" not in failure.diagnostic


def test_browser_repair_is_preserved_in_successful_run_history(tmp_path: Path, monkeypatch) -> None:
    source = make_x_source_payload(
            source_id="x_repair",
            platform="x",
            connector_id="opencli_x_repair",
            identifier="repairable",
        )
    source["connector_status"] = "supported"
    SourceRepository(tmp_path).upsert_source(source)
    task = Task(
        task_id="fetch_x_repair",
        task_type=TaskType.FETCH,
        target_kind="source",
        target_id="x_repair",
    )
    connector = _RepairableBrowserConnector()
    monkeypatch.setattr("backend.application.workflows.acquisition._build_connector", lambda base_dir, source: connector)
    monkeypatch.setattr(
        "backend.application.workflows.browser_acquisition_batch.prepare_opencli_browser_session",
        lambda **kwargs: {"status": "ready", "browser_headless": False},
    )
    monkeypatch.setattr(
        "backend.application.workflows.browser_acquisition_batch.cleanup_headless_browser_session",
        lambda: None,
    )
    from backend.application.tasks.handlers.acquisition import handle_fetch
    from backend.application.workflows.browser_acquisition_batch import browser_acquisition_batch

    with browser_acquisition_batch(tmp_path, tasks=[task]):
        result = handle_fetch(task, tmp_path)

    assert result["status"] == "success"
    run = __import__("backend.infrastructure.storage.repositories", fromlist=["RunRepository"]).RunRepository(tmp_path).list_runs()[0]
    assert run["raw_payload"]["repair_attempts"][0]["original_diagnostic"]["category"] == "browser_session"


def test_failed_browser_repair_preserves_attempt_in_task_artifacts(tmp_path: Path, monkeypatch) -> None:
    source = make_x_source_payload(
            source_id="x_repair_failure",
            connector_id="opencli_x_repair_failure",
            identifier="repairable",
        )
    source["connector_status"] = "supported"
    SourceRepository(tmp_path).upsert_source(source)
    task = Task(task_id="fetch_x_repair_failure", task_type=TaskType.FETCH, target_kind="source", target_id="x_repair_failure")
    monkeypatch.setattr(
        "backend.application.workflows.acquisition._build_connector",
        lambda base_dir, source: _RepairableBrowserConnector(),
    )
    prepared = iter(({"status": "ready", "browser_headless": False}, {"status": "blocked", "browser_headless": False}))
    monkeypatch.setattr(
        "backend.application.workflows.browser_acquisition_batch.prepare_opencli_browser_session",
        lambda **kwargs: next(prepared),
    )
    monkeypatch.setattr("backend.application.workflows.browser_acquisition_batch.cleanup_headless_browser_session", lambda: None)
    from backend.application.tasks.handlers.acquisition import handle_fetch
    from backend.application.workflows.browser_acquisition_batch import browser_acquisition_batch

    with browser_acquisition_batch(tmp_path, tasks=[task]):
        result = handle_fetch(task, tmp_path)

    assert result["status"] == "failed"
    assert result["artifacts"]["repair_attempts"][0]["repair_result"]["status"] == "blocked"


def test_browser_repair_second_fetch_stale_is_skipped(tmp_path: Path, monkeypatch) -> None:
    source = make_x_source_payload(
        source_id="x_repair_stale", connector_id="opencli_x_repair_stale", identifier="repairable"
    )
    source["connector_status"] = "supported"
    SourceRepository(tmp_path).upsert_source(source)
    task = Task(
        task_id="fetch_x_repair_stale", task_type=TaskType.FETCH,
        target_kind="source", target_id="x_repair_stale",
    )

    class StaleAfterRepair:
        calls = 0

        def fetch_new_items(self, since_cursor):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("extension disconnected")
            edited = {**source, "identifier": "edited-during-repair"}
            SourceRepository(tmp_path).upsert_source(edited)
            return FetchResult(items=[], next_cursor=None)

    connector = StaleAfterRepair()
    monkeypatch.setattr("backend.application.workflows.acquisition._build_connector", lambda *_: connector)
    monkeypatch.setattr(
        "backend.application.workflows.browser_acquisition_batch.prepare_opencli_browser_session",
        lambda **kwargs: {"status": "ready", "browser_headless": False},
    )
    monkeypatch.setattr(
        "backend.application.workflows.browser_acquisition_batch.cleanup_headless_browser_session",
        lambda: None,
    )
    from backend.application.tasks.handlers.acquisition import handle_fetch
    from backend.application.workflows.browser_acquisition_batch import browser_acquisition_batch

    with browser_acquisition_batch(tmp_path, tasks=[task]):
        result = handle_fetch(task, tmp_path)

    assert result == {
        "status": "skipped",
        "artifacts": {"reason": "source_configuration_changed"},
        "error": "source_configuration_changed",
    }
    assert connector.calls == 2
