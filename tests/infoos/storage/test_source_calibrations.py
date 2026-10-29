from __future__ import annotations

import sqlite3

import pytest

from backend.infrastructure.storage.repositories import SourceRepository


def _seed_source(base_dir, source_id: str = "rss_calibration") -> None:
    SourceRepository(base_dir).upsert_source(
        {
            "source_id": source_id,
            "platform": "rss",
            "source_type": "feed",
            "identifier": "https://example.com/feed.xml",
            "display_name": "Calibration RSS",
            "cadence_hours": 24,
            "enabled": True,
            "connector_status": "implemented_uncalibrated",
            "connector_id": source_id,
        }
    )


def _calibration_payload(*, task_id: str, calibrated_at: str, result: str = "success") -> dict:
    return {
        "calibration_id": f"cal_{task_id}",
        "source_id": "rss_calibration",
        "platform": "rss",
        "source_type": "feed",
        "identifier": "https://example.com/feed.xml",
        "result": result,
        "visible_count": 3,
        "sample_count": 1,
        "failure_class": None,
        "error_summary": None,
        "task_id": task_id,
        "calibrated_at": calibrated_at,
        "raw_payload": {"probe": "disposable"},
    }


def test_source_calibration_round_trip_and_latest(tmp_path):
    from backend.infrastructure.storage.repositories import SourceCalibrationRepository

    _seed_source(tmp_path)
    repo = SourceCalibrationRepository(tmp_path)
    repo.append(**_calibration_payload(task_id="source_calibrate_rss_v1", calibrated_at="2026-07-11T06:00:00Z"))
    repo.append(**_calibration_payload(task_id="source_calibrate_rss_v2", calibrated_at="2026-07-11T07:00:00Z"))

    assert repo.get_by_task_id("source_calibrate_rss_v1")["raw_payload"] == {"probe": "disposable"}
    assert repo.latest_for_source("rss_calibration")["task_id"] == "source_calibrate_rss_v2"


def test_record_calibration_result_updates_evidence_and_source_atomically(tmp_path):
    from backend.infrastructure.storage.repositories import SourceCalibrationRepository

    _seed_source(tmp_path)
    calibration = _calibration_payload(
        task_id="source_calibrate_rss_success",
        calibrated_at="2026-07-11T08:00:00Z",
    )

    SourceRepository(tmp_path).record_calibration_result(
        calibration=calibration,
        connector_status="supported",
    )

    assert SourceRepository(tmp_path).get_source("rss_calibration")["connector_status"] == "supported"
    assert SourceCalibrationRepository(tmp_path).get_by_task_id(calibration["task_id"])["result"] == "success"


def test_record_calibration_result_rolls_back_source_when_evidence_insert_fails(tmp_path, monkeypatch):
    from backend.infrastructure.storage.source_calibrations import SourceCalibrationRepository

    _seed_source(tmp_path)

    def fail_append(*_args, **_kwargs):
        raise sqlite3.IntegrityError("forced calibration insert failure")

    monkeypatch.setattr(SourceCalibrationRepository, "append_to_connection", fail_append)

    with pytest.raises(sqlite3.IntegrityError, match="forced calibration insert failure"):
        SourceRepository(tmp_path).record_calibration_result(
            calibration=_calibration_payload(
                task_id="source_calibrate_rss_failure",
                calibrated_at="2026-07-11T09:00:00Z",
            ),
            connector_status="supported",
        )

    assert SourceRepository(tmp_path).get_source("rss_calibration")["connector_status"] == "implemented_uncalibrated"


def test_source_rows_decode_raw_metadata_and_delete_calibration_evidence(tmp_path):
    from backend.infrastructure.storage.repositories import SourceCalibrationRepository

    _seed_source(tmp_path)
    source = SourceRepository(tmp_path).get_source("rss_calibration")
    assert source["raw_metadata"] == {}
    SourceRepository(tmp_path).record_calibration_result(
        calibration=_calibration_payload(
            task_id="source_calibrate_delete",
            calibrated_at="2026-07-11T10:00:00Z",
        ),
        connector_status="supported",
    )

    assert SourceRepository(tmp_path).delete_source("rss_calibration") is True
    assert SourceCalibrationRepository(tmp_path).get_by_task_id("source_calibrate_delete") is None
