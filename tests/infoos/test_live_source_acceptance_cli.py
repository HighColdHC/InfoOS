from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.infrastructure.storage.repositories import TaskEventRepository

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "run-infoos-live-source-acceptance.py"


def _module():
    spec = importlib.util.spec_from_file_location("live_source_acceptance", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_live_acceptance_management_post_sends_same_origin(monkeypatch) -> None:
    module = _module()
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b"{}"

    monkeypatch.setattr(module, "urlopen", lambda request, timeout: requests.append(request) or Response())
    assert module._request_json("http://127.0.0.1:8000/api/source-run-batches", "POST", {}) == {}
    assert requests[0].get_header("Origin") == "http://127.0.0.1:8000"


def test_live_acceptance_requires_explicit_source_ids(tmp_path: Path) -> None:
    module = _module()

    with pytest.raises(ValueError, match="at least one public and one browser source"):
        module.validate_config(
            base_dir=tmp_path,
            public_source_ids=[],
            browser_source_ids=[],
            runs_per_source=10,
        )


def test_live_acceptance_refuses_less_than_twenty_total_runs(tmp_path: Path) -> None:
    module = _module()

    with pytest.raises(ValueError, match="at least 20"):
        module.validate_config(
            base_dir=tmp_path,
            public_source_ids=["rss-public"],
            browser_source_ids=["x-browser"],
            runs_per_source=9,
        )


def test_live_acceptance_separates_public_browser_and_blocked() -> None:
    module = _module()

    report = module.build_report(
        attempts=[
            {"source_id": "rss-public", "group": "public", "classification": "success"},
            {"source_id": "x-browser", "group": "browser", "classification": "success"},
            {"source_id": "x-browser", "group": "browser", "classification": "blocked_auth"},
        ],
        public_min_rate=0.98,
        browser_min_rate=0.90,
        expired_leases=0,
        oldest_running_seconds=None,
        lease_seconds=300,
    )

    assert report["groups"]["public"]["counts"] == {"success": 1}
    assert report["groups"]["browser"]["counts"] == {"success": 1, "blocked_auth": 1}
    assert report["status"] == "blocked"
    assert any(item["metric"] == "browser_blocked_auth" for item in report["violations"])


def test_live_acceptance_does_not_count_auth_block_as_connector_success() -> None:
    module = _module()

    report = module.build_report(
        attempts=[
            {"source_id": "rss-public", "group": "public", "classification": "success"},
            {"source_id": "x-browser", "group": "browser", "classification": "blocked_auth"},
        ],
        public_min_rate=0.98,
        browser_min_rate=0.90,
        expired_leases=0,
        oldest_running_seconds=None,
        lease_seconds=300,
    )

    assert report["groups"]["browser"]["connector_successes"] == 0
    assert report["groups"]["browser"]["connector_attempts"] == 0
    assert report["groups"]["browser"]["success_rate"] is None


def test_live_acceptance_exits_nonzero_below_threshold() -> None:
    module = _module()

    report = module.build_report(
        attempts=[
            {"source_id": "rss-public", "group": "public", "classification": "connector_failure"},
            {"source_id": "x-browser", "group": "browser", "classification": "success"},
        ],
        public_min_rate=0.98,
        browser_min_rate=0.90,
        expired_leases=0,
        oldest_running_seconds=None,
        lease_seconds=300,
    )

    assert report["status"] == "blocked"
    assert any(item["metric"] == "public_success_rate" for item in report["violations"])


def test_live_acceptance_does_not_hide_platform_failure_in_browser_group() -> None:
    module = _module()
    attempts = [
        {
            "source_id": "rss-public",
            "platform": "rss",
            "group": "public",
            "classification": "success",
        },
        {
            "source_id": "x-browser",
            "platform": "x",
            "group": "browser",
            "classification": "success",
        },
        {
            "source_id": "x-browser",
            "platform": "x",
            "group": "browser",
            "classification": "connector_failure",
        },
    ]
    attempts.extend(
        {
            "source_id": f"youtube-{index}",
            "platform": "youtube",
            "group": "browser",
            "classification": "success",
        }
        for index in range(9)
    )

    report = module.build_report(
        attempts=attempts,
        public_min_rate=0.98,
        browser_min_rate=0.90,
        expired_leases=0,
        oldest_running_seconds=None,
        lease_seconds=300,
    )

    assert report["groups"]["browser"]["success_rate"] >= 0.90
    assert report["platforms"]["x"]["success_rate"] == 0.5
    assert report["platforms"]["x"]["status"] == "blocked"
    assert report["platforms"]["youtube"]["status"] == "pass"
    assert report["status"] == "blocked"


def test_live_acceptance_rejects_preexisting_manual_fetch_task() -> None:
    module = _module()

    assert module.is_fresh_manual_task(
        {"created_at": "2026-07-08T15:02:23Z"},
        submitted_at="2026-07-11T02:31:59Z",
    ) is False


def test_live_acceptance_blocks_an_incomplete_planned_run_set() -> None:
    module = _module()

    report = module.build_report(
        attempts=[
            {"source_id": "rss-public", "group": "public", "classification": "success"},
            {"source_id": "x-browser", "group": "browser", "classification": "timeout"},
        ],
        public_min_rate=0.98,
        browser_min_rate=0.90,
        expired_leases=0,
        oldest_running_seconds=None,
        lease_seconds=300,
        planned_runs=20,
    )

    assert report["executed_runs"] == 2
    assert any(item["metric"] == "completed_runs" for item in report["violations"])


def test_live_acceptance_rejects_terminal_chain_without_its_own_output(tmp_path: Path) -> None:
    module = _module()
    _seed_terminal_chain(tmp_path, chain_id="no-output")

    result = module._read_attempt_artifacts(tmp_path, "fetch-no-output")

    assert result["classification"] == "pipeline_failure"
    assert result["output"]["missing_output_card_ids"] == ["card-no-output"]


def test_live_acceptance_accepts_terminal_chain_with_explicit_ignore(tmp_path: Path) -> None:
    module = _module()
    _seed_terminal_chain(
        tmp_path,
        chain_id="explicit-ignore",
        output_artifacts={"explicit_no_output": True, "route": "ignore"},
    )

    result = module._read_attempt_artifacts(tmp_path, "fetch-explicit-ignore")

    assert result["classification"] == "success"
    assert result["output"]["explicit_ignore_card_ids"] == ["card-explicit-ignore"]


def test_live_acceptance_waits_for_retry_chain_to_reach_explicit_block(monkeypatch, tmp_path: Path) -> None:
    module = _module()
    statuses = iter([
        ["success", "pending"],
        ["success", "blocked"],
    ])
    monkeypatch.setattr(module, "_chain_task_statuses", lambda _base_dir, _chain_id: next(statuses))
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    terminal = module._wait_for_chain_terminal(
        tmp_path,
        "chain-retry",
        timeout_seconds=1,
        poll_seconds=0.01,
    )

    assert terminal == ["success", "blocked"]


def test_live_acceptance_redacts_worker_stderr_before_report() -> None:
    module = _module()

    safe = module.safe_stderr_tail(
        "Authorization: Bearer secret-token Cookie: session=secret-cookie "
        "https://example.com/file?token=signed-secret"
    )

    assert "secret-token" not in safe
    assert "secret-cookie" not in safe
    assert "signed-secret" not in safe


def _seed_terminal_chain(
    base_dir: Path,
    *,
    chain_id: str,
    output_artifacts: dict[str, object] | None = None,
) -> None:
    repo = TaskRepository.for_base_dir(base_dir)
    card_id = f"card-{chain_id}"
    repo.enqueue(
        Task(
            task_id=f"fetch-{chain_id}",
            task_type=TaskType.FETCH,
            target_kind="source",
            target_id="source-test",
            status=TaskStatus.SUCCESS,
            artifacts_summary={"chain_id": chain_id, "raw_item_ids": [f"raw-{chain_id}"]},
        )
    )
    repo.enqueue(
        Task(
            task_id=f"card-build-{chain_id}",
            task_type=TaskType.CARD_BUILD,
            target_kind="raw_item",
            target_id=f"raw-{chain_id}",
            status=TaskStatus.SUCCESS,
            artifacts_summary={"chain_id": chain_id, "card_id": card_id},
        )
    )
    repo.enqueue(
        Task(
            task_id=f"output-{chain_id}",
            task_type=TaskType.OUTPUT_GENERATE,
            target_kind="card",
            target_id=card_id,
            status=TaskStatus.SUCCESS,
            artifacts_summary={"chain_id": chain_id} | (output_artifacts or {}),
        )
    )
    TaskEventRepository(base_dir).append(
        event_type="acquisition_committed",
        task_id=f"fetch-{chain_id}",
        chain_id=chain_id,
        status="success",
        payload={"raw_item_count": 1},
    )
