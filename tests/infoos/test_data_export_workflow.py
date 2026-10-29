from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from backend.application.workflows.data_export import build_export_artifact
from backend.application.workflows import data_export as data_export_workflow
from backend.application.tasks.handlers.data_export import handle_data_export
from backend.domain.tasks import Task, TaskType
from tests.infoos.storage.migration_test_helpers import migrate_database_path


def _runtime(tmp_path: Path) -> Path:
    db = migrate_database_path(tmp_path / "data" / "infoos.sqlite3")
    with sqlite3.connect(db) as conn:
        conn.execute(
            """insert into sources (
              source_id, platform, source_type, identifier, display_name, cadence_hours,
              connector_status, raw_metadata, created_at, updated_at
            ) values ('source-1','rss','feed','https://example.com/feed','Example',12,
                      'ready','{}','2026-07-17T00:00:00Z','2026-07-17T00:00:00Z')"""
        )
        conn.execute(
            """insert into cards (
              card_id, card_type, version, content_hash, updated_at, status,
              completeness_status, source_id, source_platform, source_type, source_url,
              title, content, summary, captured_at, processed_at
            ) values ('card-1','information',1,'hash','2026-07-17T00:00:00Z','active',
                      'complete','source-1','rss','feed','https://example.com/post',
                      'Title','Body','Summary','2026-07-17T00:00:00Z','2026-07-17T00:00:00Z')"""
        )
        conn.execute(
            """insert into card_content_blocks (
              block_id, card_id, position, layer, block_kind, status, body,
              created_at, updated_at
            ) values ('block-1','card-1',0,'source','text','ready','Block',
                      '2026-07-17T00:00:00Z','2026-07-17T00:00:00Z')"""
        )
    return tmp_path


def _assert_manifest_matches(bundle: zipfile.ZipFile) -> dict[str, object]:
    manifest = json.loads(bundle.read("manifest.json"))
    assert manifest["generated_at"].endswith("Z")
    listed = {entry["path"] for entry in manifest["files"]}
    assert listed | {"manifest.json"} == set(bundle.namelist())
    for entry in manifest["files"]:
        content = bundle.read(entry["path"])
        assert entry["size_bytes"] == len(content)
        assert entry["sha256"] == hashlib.sha256(content).hexdigest()
    return manifest


def test_data_json_contains_only_current_collector_objects_and_integrity(tmp_path: Path) -> None:
    base_dir = _runtime(tmp_path)
    artifact = build_export_artifact(
        base_dir,
        export_id="export-1",
        request={
            "kind": "data",
            "format": "json",
            "time_from": None,
            "time_to": None,
            "include_sources_runs": True,
            "include_cards_content": True,
            "include_asset_index": True,
            "include_attachment_files": False,
            "include_redacted_logs": False,
            "include_media_files": False,
            "log_levels": [],
            "include_environment_snapshot": False,
            "source_run_ids": [],
        },
    )

    path = base_dir / "data" / artifact["logical_location"]
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema"] == "infoos.export.v1"
    assert payload["sources"][0]["source_id"] == "source-1"
    assert payload["cards"][0]["card_id"] == "card-1"
    assert payload["content_blocks"][0]["block_id"] == "block-1"
    assert not ({"topics", "decisions", "outputs", "events"} & payload.keys())
    assert artifact["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert artifact["size_bytes"] == path.stat().st_size
    assert not list(path.parent.glob("*.partial"))


def test_data_zip_only_includes_db_owned_safe_attachment(tmp_path: Path) -> None:
    base_dir = _runtime(tmp_path)
    assets = base_dir / "data" / "assets"
    assets.mkdir(parents=True)
    (assets / "safe.txt").write_text("safe", encoding="utf-8")
    outside = base_dir / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    db = base_dir / "data" / "infoos.sqlite3"
    with sqlite3.connect(db) as conn:
        for asset_id, local_path in (("safe", "safe.txt"), ("unsafe", "../../secret.txt")):
            conn.execute(
                """insert into card_assets (
                  asset_id,card_id,asset_kind,source_url,local_path,mime_type,size_bytes,
                  preservation_class,status,cleanup_state,created_at,updated_at
                ) values (?,?, 'image','https://example.com/a',?,'text/plain',4,
                          'cached','downloaded','present','2026-07-17T00:00:00Z','2026-07-17T00:00:00Z')""",
                (asset_id, "card-1", local_path),
            )

    request = {
        "kind": "data", "format": "zip", "time_from": None, "time_to": None,
        "include_sources_runs": False, "include_cards_content": True,
        "include_asset_index": True, "include_attachment_files": True,
        "include_redacted_logs": False, "include_media_files": False,
        "log_levels": [], "include_environment_snapshot": False, "source_run_ids": [],
    }
    artifact = build_export_artifact(base_dir, export_id="export-2", request=request)

    path = base_dir / "data" / artifact["logical_location"]
    with zipfile.ZipFile(path) as bundle:
        assert "attachments/safe/safe.txt" in bundle.namelist()
        assert all("unsafe" not in name for name in bundle.namelist())
        assert bundle.read("attachments/safe/safe.txt") == b"safe"
        _assert_manifest_matches(bundle)


def test_backup_uses_consistent_sqlite_snapshot(tmp_path: Path) -> None:
    base_dir = _runtime(tmp_path)
    request = {
        "kind": "backup", "format": "zip", "time_from": None, "time_to": None,
        "include_sources_runs": False, "include_cards_content": False,
        "include_asset_index": True, "include_attachment_files": False,
        "include_redacted_logs": False, "include_media_files": False,
        "log_levels": [], "include_environment_snapshot": False, "source_run_ids": [],
    }
    artifact = build_export_artifact(base_dir, export_id="export-3", request=request)
    path = base_dir / "data" / artifact["logical_location"]

    with zipfile.ZipFile(path) as bundle:
        _assert_manifest_matches(bundle)
        snapshot = base_dir / "snapshot.sqlite3"
        snapshot.write_bytes(bundle.read("infoos.sqlite3"))
    with sqlite3.connect(snapshot) as conn:
        assert conn.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert conn.execute("select title from cards where card_id='card-1'").fetchone()[0] == "Title"


def test_handler_reuses_matching_owned_artifact(tmp_path: Path, monkeypatch) -> None:
    base_dir = _runtime(tmp_path)
    request = {
        "kind": "data", "format": "json", "time_from": None, "time_to": None,
        "include_sources_runs": False, "include_cards_content": True,
        "include_asset_index": False, "include_attachment_files": False,
        "include_redacted_logs": False, "include_media_files": False,
        "log_levels": [], "include_environment_snapshot": False, "source_run_ids": [],
    }
    first = handle_data_export(
        Task("task-1", TaskType.DATA_EXPORT, "export", "export-4", artifacts_summary={
            "export_id": "export-4", "kind": "data", "request": request,
        }),
        base_dir,
    )
    task = Task(
        "task-1", TaskType.DATA_EXPORT, "export", "export-4",
        artifacts_summary={
            "export_id": "export-4", "kind": "data", "request": request,
            "artifact": first["artifacts"]["artifact"],
        },
    )

    monkeypatch.setattr(
        "backend.application.tasks.handlers.data_export.build_export_artifact",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should reuse")),
    )
    second = handle_data_export(task, base_dir)

    assert second["status"] == "success"
    assert second["artifacts"]["artifact"] == first["artifacts"]["artifact"]


def test_diagnostic_bundle_redacts_logs_and_reads_persisted_run_summary(
    tmp_path: Path, monkeypatch
) -> None:
    base_dir = _runtime(tmp_path)
    state_home = base_dir / "state"
    state_home.mkdir()
    monkeypatch.setenv("INFOOS_STATE_HOME", str(state_home))
    monkeypatch.setenv("INFOOS_DATA_HOME", str(base_dir / "data"))
    (state_home / "setup-state.json").write_text(
        json.dumps({"session": {"context_id": "secret-session"}, "components": {}}),
        encoding="utf-8",
    )
    logs = base_dir / "data" / "logs"
    logs.mkdir(parents=True)
    logs.joinpath("infoos.log").write_text(
        f"2026-07-17 11:00:00,000 INFO infoos.fetch token=ios_{'x' * 24} path={base_dir / 'data'}\n"
        "2026-07-17 12:00:00,000 ERROR infoos.fetch request failed\n",
        encoding="utf-8",
    )
    with sqlite3.connect(base_dir / "data" / "infoos.sqlite3") as conn:
        conn.execute(
            """insert into tasks (
              task_id,task_type,target_kind,target_id,status,input_version,created_at,updated_at
            ) values ('fetch-1','fetch','source','source-1','success','v1',
                      '2026-07-17T00:00:00Z','2026-07-17T00:00:00Z')"""
        )
    request = {
        "kind": "diagnostic", "format": "zip", "time_from": None, "time_to": None,
        "include_sources_runs": False, "include_cards_content": False,
        "include_asset_index": False, "include_attachment_files": False,
        "include_redacted_logs": True, "include_media_files": False,
        "log_levels": ["ERROR"], "include_environment_snapshot": True,
        "source_run_ids": [
            "task_" + hashlib.sha256(b"infoos-source-run:task\0fetch-1").hexdigest()[:24]
        ],
    }

    artifact = build_export_artifact(base_dir, export_id="export-5", request=request)
    path = base_dir / "data" / artifact["logical_location"]
    with zipfile.ZipFile(path) as bundle:
        _assert_manifest_matches(bundle)
        diagnostic_text = bundle.read("diagnostic.json").decode()
        diagnostic = json.loads(diagnostic_text)

    assert "ios_" not in diagnostic_text
    assert str(base_dir / "data") not in diagnostic_text
    assert len(diagnostic["logs"]) == 1
    assert diagnostic["logs"][0]["level"] == "ERROR"
    assert diagnostic["logs"][0]["message"] == "request failed"
    assert "session" not in diagnostic["environment_snapshot"]
    assert "components" in diagnostic["environment_snapshot"]
    assert diagnostic["source_runs"][0]["task_id"] == "fetch-1"
    assert not ({"topics", "decisions", "outputs", "events"} & diagnostic.keys())


def test_data_export_applies_time_range_to_runs_tasks_and_cards(tmp_path: Path) -> None:
    base_dir = _runtime(tmp_path)
    request = {
        "kind": "data", "format": "json",
        "time_from": "2026-07-18T00:00:00Z", "time_to": "2026-07-19T00:00:00Z",
        "include_sources_runs": True, "include_cards_content": True,
        "include_asset_index": True, "include_attachment_files": False,
        "include_redacted_logs": False, "include_media_files": False,
        "log_levels": [], "include_environment_snapshot": False, "source_run_ids": [],
    }

    artifact = build_export_artifact(base_dir, export_id="export-6", request=request)
    payload = json.loads(
        (base_dir / "data" / artifact["logical_location"]).read_text(encoding="utf-8")
    )

    assert payload["cards"] == []
    assert payload["content_blocks"] == []
    assert payload["runs"] == []
    assert payload["tasks"] == []
    assert payload["task_events"] == []


def test_data_time_range_accepts_each_stable_business_timestamp(tmp_path: Path) -> None:
    base_dir = _runtime(tmp_path)
    with sqlite3.connect(base_dir / "data" / "infoos.sqlite3") as conn:
        conn.execute(
            "update cards set published_at='2026-07-18T12:00:00Z' where card_id='card-1'"
        )
        conn.execute(
            """insert into run_history (
              run_id,source_id,scheduled_for,started_at,finished_at,status,trigger
            ) values ('run-1','source-1','2026-07-18T12:00:00Z',
                      '2026-07-20T00:00:00Z','2026-07-20T00:01:00Z','success','manual')"""
        )
    request = {
        "kind": "data", "format": "json",
        "time_from": "2026-07-18T00:00:00Z", "time_to": "2026-07-19T00:00:00Z",
        "include_sources_runs": True, "include_cards_content": True,
        "include_asset_index": True, "include_attachment_files": False,
        "include_redacted_logs": False, "include_media_files": False,
        "log_levels": [], "include_environment_snapshot": False, "source_run_ids": [],
    }

    artifact = build_export_artifact(base_dir, export_id="export-7", request=request)
    payload = json.loads(
        (base_dir / "data" / artifact["logical_location"]).read_text(encoding="utf-8")
    )

    assert [card["card_id"] for card in payload["cards"]] == ["card-1"]
    assert [block["block_id"] for block in payload["content_blocks"]] == ["block-1"]
    assert [run["run_id"] for run in payload["runs"]] == ["run-1"]


def test_failed_build_removes_only_its_partial_file(tmp_path: Path, monkeypatch) -> None:
    base_dir = _runtime(tmp_path)
    export_dir = base_dir / "data" / "exports" / "export-fail"
    export_dir.mkdir(parents=True)
    unrelated = export_dir / "keep.partial"
    unrelated.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(
        data_export_workflow,
        "_write_data",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(OSError):
        build_export_artifact(
            base_dir,
            export_id="export-fail",
            request={
                "kind": "data", "format": "json", "include_cards_content": True,
                "include_sources_runs": False, "include_asset_index": True,
                "include_attachment_files": False, "include_redacted_logs": False,
                "include_media_files": False, "include_environment_snapshot": False,
                "log_levels": [], "source_run_ids": [], "time_from": None, "time_to": None,
            },
        )

    assert unrelated.read_text(encoding="utf-8") == "keep"
    assert not (export_dir / "infoos-data.json.partial").exists()


def test_handler_rebuilds_corrupted_artifact(tmp_path: Path) -> None:
    base_dir = _runtime(tmp_path)
    request = {
        "kind": "data", "format": "json", "time_from": None, "time_to": None,
        "include_sources_runs": False, "include_cards_content": True,
        "include_asset_index": True, "include_attachment_files": False,
        "include_redacted_logs": False, "include_media_files": False,
        "log_levels": [], "include_environment_snapshot": False, "source_run_ids": [],
    }
    first = handle_data_export(
        Task("task-corrupt", TaskType.DATA_EXPORT, "export", "export-corrupt",
             artifacts_summary={"export_id": "export-corrupt", "kind": "data", "request": request}),
        base_dir,
    )
    artifact = first["artifacts"]["artifact"]
    path = base_dir / "data" / artifact["logical_location"]
    path.write_text("corrupt", encoding="utf-8")

    rebuilt = handle_data_export(
        Task("task-corrupt", TaskType.DATA_EXPORT, "export", "export-corrupt",
             artifacts_summary={
                 "export_id": "export-corrupt", "kind": "data", "request": request,
                 "artifact": artifact,
             }),
        base_dir,
    )["artifacts"]["artifact"]

    assert json.loads(path.read_text(encoding="utf-8"))["schema"] == "infoos.export.v1"
    assert rebuilt["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
