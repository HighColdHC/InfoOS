from __future__ import annotations

import pytest

from backend.domain.export_contracts import (
    ExportRequestError,
    export_request_input_version,
    normalize_export_request,
)


def test_data_export_with_attachments_is_normalized_to_zip_with_asset_index() -> None:
    request = normalize_export_request(
        {
            "kind": "data",
            "format": "json",
            "include_cards_content": True,
            "include_attachment_files": True,
            "log_levels": [],
            "source_run_ids": [],
        }
    )

    assert request == {
        "kind": "data",
        "format": "zip",
        "time_from": None,
        "time_to": None,
        "include_sources_runs": False,
        "include_cards_content": True,
        "include_asset_index": True,
        "include_attachment_files": True,
        "include_redacted_logs": False,
        "include_media_files": False,
        "log_levels": [],
        "include_environment_snapshot": False,
        "source_run_ids": [],
    }


def test_backup_and_diagnostic_clear_irrelevant_fields() -> None:
    backup = normalize_export_request(
        {"kind": "backup", "format": "json", "include_media_files": True}
    )
    diagnostic = normalize_export_request(
        {
            "kind": "diagnostic",
            "format": "json",
            "include_environment_snapshot": True,
            "source_run_ids": ["run-2", "run-1", "run-2"],
            "log_levels": ["warn", "ERROR", "warn"],
        }
    )

    assert backup["format"] == "zip"
    assert backup["include_asset_index"] is True
    assert backup["include_media_files"] is True
    assert backup["include_cards_content"] is False
    assert diagnostic["format"] == "zip"
    assert diagnostic["include_redacted_logs"] is True
    assert diagnostic["include_environment_snapshot"] is True
    assert diagnostic["source_run_ids"] == ["run-2", "run-1"]
    assert diagnostic["log_levels"] == ["WARNING", "ERROR"]


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"kind": "wrong"}, "invalid_export_kind"),
        ({"kind": "data", "format": "csv"}, "invalid_export_format"),
        (
            {"kind": "data", "time_from": "2026-07-18", "time_to": "2026-07-17"},
            "invalid_export_time_range",
        ),
        ({"kind": "diagnostic", "log_levels": ["SECRET"]}, "invalid_log_level"),
        ({"kind": "data"}, "empty_data_export"),
    ],
)
def test_invalid_export_requests_have_stable_error_codes(
    payload: dict[str, object], code: str
) -> None:
    with pytest.raises(ExportRequestError) as raised:
        normalize_export_request(payload)

    assert raised.value.code == code


def test_export_request_input_version_is_canonical() -> None:
    left = normalize_export_request(
        {"kind": "data", "include_cards_content": True, "include_sources_runs": True}
    )
    right = dict(reversed(list(left.items())))

    assert export_request_input_version(left) == export_request_input_version(right)
    assert export_request_input_version(left).startswith("export:v1:")


def test_asset_index_defaults_to_enabled() -> None:
    request = normalize_export_request({"kind": "data", "include_cards_content": True})

    assert request["include_asset_index"] is True
