from __future__ import annotations

from copy import deepcopy
import sqlite3

import pytest

from backend.infrastructure.storage.source_config_versions import SourceConfigVersionRepository
from backend.infrastructure.storage.database import connect_writable
from backend.infrastructure.storage.sources import SourceRepository


def _source(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "source_id": "source-x-openai",
        "platform": "x",
        "source_type": "creator",
        "identifier": "OpenAI",
        "display_name": "OpenAI",
        "default_domain_id": "legacy-domain-must-not-return",
        "cadence_hours": 6,
        "notes": "Official account",
        "enabled": True,
        "connector_status": "implemented_uncalibrated",
        "connector_id": "opencli-x",
        "raw_metadata": {
            "max_items_per_run": 20,
            "auto_enrich_media": True,
            "command": ["twitter", "tweets", "OpenAI", "--limit", "20"],
        },
    }
    payload.update(overrides)
    return payload


def test_save_configuration_writes_source_and_immutable_version(tmp_path) -> None:
    repo = SourceRepository(tmp_path)

    saved = repo.save_configuration(_source(), change_reason="created")

    source = repo.get_source("source-x-openai")
    versions = repo.list_versions("source-x-openai", limit=10)
    assert saved == source
    assert source is not None
    assert source["default_domain_id"] is None
    assert len(versions) == 1
    assert versions[0]["change_reason"] == "created"
    assert versions[0]["config_json"]["display_name"] == "OpenAI"


def test_display_change_adds_config_version_without_changing_fetch_version(tmp_path) -> None:
    repo = SourceRepository(tmp_path)
    repo.save_configuration(_source(), change_reason="created")
    renamed = _source(display_name="OpenAI News", notes="Display update")

    repo.save_configuration(renamed, change_reason="display_changed")

    versions = repo.list_versions("source-x-openai", limit=10)
    assert len(versions) == 2
    assert versions[0]["config_version"] != versions[1]["config_version"]
    assert versions[0]["fetch_input_version"] == versions[1]["fetch_input_version"]


def test_set_enabled_adds_version_without_deleting_history(tmp_path) -> None:
    repo = SourceRepository(tmp_path)
    repo.save_configuration(_source(), change_reason="created")

    disabled = repo.set_enabled("source-x-openai", False)

    assert disabled is not None
    assert disabled["enabled"] is False
    versions = repo.list_versions("source-x-openai", limit=10)
    assert len(versions) == 2
    assert versions[0]["change_reason"] == "activation_changed"
    assert versions[0]["fetch_input_version"] == versions[1]["fetch_input_version"]
    assert repo.set_enabled("missing-source", False) is None


def test_stable_identity_lookup_normalizes_case_and_surrounding_whitespace(tmp_path) -> None:
    repo = SourceRepository(tmp_path)
    repo.save_configuration(_source(), change_reason="created")

    found = repo.find_by_stable_identity(" X ", " CREATOR ", " openai ")

    assert found is not None
    assert found["source_id"] == "source-x-openai"
    assert repo.find_by_stable_identity("x", "creator", "anthropic") is None


@pytest.mark.parametrize(
    "change_reason",
    ["", "renamed", "delete", "created ", "FETCH_CONFIG_CHANGED"],
)
def test_save_configuration_rejects_unknown_change_reason(tmp_path, change_reason: str) -> None:
    repo = SourceRepository(tmp_path)

    with pytest.raises(ValueError, match="change_reason"):
        repo.save_configuration(_source(), change_reason=change_reason)

    assert repo.get_source("source-x-openai") is None


def test_save_configuration_rolls_back_source_when_version_append_fails(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = SourceRepository(tmp_path)
    repo.save_configuration(_source(), change_reason="created")
    previous = repo.get_source("source-x-openai")

    def fail_append(*_args, **_kwargs):
        raise sqlite3.IntegrityError("forced version failure")

    monkeypatch.setattr(SourceConfigVersionRepository, "append_to_connection", fail_append)

    with pytest.raises(sqlite3.IntegrityError, match="forced version failure"):
        repo.save_configuration(
            _source(display_name="Must roll back"),
            change_reason="display_changed",
        )

    assert repo.get_source("source-x-openai") == previous
    assert len(repo.list_versions("source-x-openai", limit=10)) == 1


def test_caller_owned_connection_is_not_committed(tmp_path) -> None:
    repo = SourceRepository(tmp_path)
    conn = connect_writable(repo.db_path)
    try:
        repo.save_configuration(_source(), change_reason="created", connection=conn)
        assert conn.in_transaction is True
        conn.rollback()
    finally:
        conn.close()

    assert repo.get_source("source-x-openai") is None
    assert repo.list_versions("source-x-openai", limit=10) == []


def test_historical_upsert_source_remains_compatible_and_does_not_write_versions(tmp_path) -> None:
    repo = SourceRepository(tmp_path)
    legacy = deepcopy(_source(default_domain_id=None))

    result = repo.upsert_source(legacy)

    assert result is None
    assert repo.get_source("source-x-openai") is not None
    assert repo.list_versions("source-x-openai", limit=10) == []
