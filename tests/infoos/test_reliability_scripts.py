from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.infrastructure.storage.repositories import RunRepository, SourceRepository
from tests.infoos._factories import make_source_payload


ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE_SCRIPT = ROOT / "scripts" / "run-infoos-reliability-acceptance.sh"
REPORT_SCRIPT = ROOT / "scripts" / "infoos-reliability-report.py"


def _test_environment(home: Path) -> dict[str, str]:
    state_home = home / ".infoos-test"
    return {
        **os.environ,
        "HOME": str(home),
        "INFOOS_STATE_HOME": str(state_home),
        "INFOOS_DATA_HOME": str(state_home / "data"),
        "INFOOS_CONFIG_PATH": str(state_home / "config.yaml"),
    }


def _seed_successful_run(base_dir: Path, *, source_id: str, platform: str) -> None:
    SourceRepository(base_dir).upsert_source(
        make_source_payload(
            source_id=source_id,
            platform=platform,
            connector_id=source_id,
            identifier=f"https://example.com/{source_id}.xml",
        )
    )
    timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    RunRepository(base_dir).append_run(
        {
            "run_id": f"{source_id}-run",
            "source_id": source_id,
            "scheduled_for": timestamp,
            "started_at": timestamp,
            "finished_at": timestamp,
            "status": "success",
            "task_type": "fetch",
        }
    )


def _seed_required_acquisition_groups(base_dir: Path) -> None:
    _seed_successful_run(base_dir, source_id="report-rss", platform="rss")
    _seed_successful_run(base_dir, source_id="report-reddit", platform="reddit")


def _run_report(
    base_dir: Path,
    output_json: Path,
    output_markdown: Path,
    *extra_args: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(REPORT_SCRIPT),
            "--base-dir",
            str(base_dir),
            "--output-json",
            str(output_json),
            "--output-markdown",
            str(output_markdown),
            *extra_args,
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )


def test_acceptance_script_refuses_default_infoos_home(tmp_path: Path) -> None:
    result = subprocess.run(
        ["bash", str(ACCEPTANCE_SCRIPT)],
        cwd=ROOT,
        env={**os.environ, "HOME": str(tmp_path)},
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "INFOOS_STATE_HOME" in result.stderr


def test_acceptance_script_requires_infoos_test_paths(tmp_path: Path) -> None:
    env = _test_environment(tmp_path)
    env["INFOOS_DATA_HOME"] = str(tmp_path / "wrong-data")
    result = subprocess.run(
        ["bash", str(ACCEPTANCE_SCRIPT)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "INFOOS_DATA_HOME" in result.stderr


def test_reliability_report_reads_sqlite(tmp_path: Path) -> None:
    _seed_required_acquisition_groups(tmp_path)
    output_json = tmp_path / "report.json"
    output_markdown = tmp_path / "report.md"

    result = _run_report(tmp_path, output_json, output_markdown)

    assert result.returncode == 0, result.stderr
    report = json.loads(output_json.read_text(encoding="utf-8"))
    assert report["reliability"]["acquisition"]["rss"]["runs_24h"] == 1
    assert report["rates"]["public"]["success_rate"] == 1.0
    assert report["rates"]["browser"]["success_rate"] == 1.0


def test_reliability_report_blocks_when_no_acquisition_groups_have_runs(tmp_path: Path) -> None:
    result = _run_report(tmp_path, tmp_path / "report.json", tmp_path / "report.md")

    assert result.returncode == 1
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "blocked"
    assert {violation["metric"] for violation in report["violations"]} == {
        "public_acquisition_readiness",
        "browser_acquisition_readiness",
    }
    assert {violation["reason"] for violation in report["violations"]} == {"no_runs_24h"}


def test_reliability_report_blocks_when_required_browser_group_has_no_runs(tmp_path: Path) -> None:
    _seed_successful_run(tmp_path, source_id="report-rss", platform="rss")

    result = _run_report(tmp_path, tmp_path / "report.json", tmp_path / "report.md")

    assert result.returncode == 1
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["rates"]["public"]["success_rate"] == 1.0
    assert report["rates"]["browser"]["runs_24h"] == 0
    assert report["rates"]["browser"]["success_rate"] is None
    assert report["violations"] == [{
        "metric": "browser_acquisition_readiness",
        "reason": "no_runs_24h",
        "actual": 0,
        "minimum": 1,
    }]


def test_reliability_report_can_require_only_browser_group(tmp_path: Path) -> None:
    _seed_successful_run(tmp_path, source_id="report-reddit", platform="reddit")

    result = _run_report(
        tmp_path,
        tmp_path / "report.json",
        tmp_path / "report.md",
        "--required-acquisition-group",
        "browser",
    )

    assert result.returncode == 0, result.stderr
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "passed"
    assert report["thresholds"]["required_acquisition_groups"] == ["browser"]
    assert report["rates"]["public"]["runs_24h"] == 0
    assert report["violations"] == []


def test_reliability_report_exits_nonzero_on_violation(tmp_path: Path) -> None:
    TaskRepository.for_base_dir(tmp_path).enqueue(
        Task(
            task_id="expired-lease",
            task_type=TaskType.FETCH,
            target_kind="source",
            target_id="source",
            status=TaskStatus.RUNNING,
            lease_expires_at="2020-01-01T00:00:00Z",
        )
    )

    result = _run_report(tmp_path, tmp_path / "report.json", tmp_path / "report.md")

    assert result.returncode == 1
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert any(violation["metric"] == "expired_leases" for violation in report["violations"])


def test_reliability_report_writes_json_and_markdown(tmp_path: Path) -> None:
    _seed_required_acquisition_groups(tmp_path)
    output_json = tmp_path / "nested" / "report.json"
    output_markdown = tmp_path / "nested" / "report.md"

    result = _run_report(tmp_path, output_json, output_markdown)

    assert result.returncode == 0, result.stderr
    assert json.loads(output_json.read_text(encoding="utf-8"))["status"] == "passed"
    markdown = output_markdown.read_text(encoding="utf-8")
    assert "# InfoOS Reliability Acceptance Report" in markdown
    assert "passed" in markdown
