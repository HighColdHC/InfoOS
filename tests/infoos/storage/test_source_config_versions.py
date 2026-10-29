from __future__ import annotations

from copy import deepcopy
import json
import sqlite3

import pytest

from backend.domain.sources.versioning import source_fetch_input_version
from tests.infoos.storage.migration_test_helpers import migrate_database_path
from backend.infrastructure.storage.source_config_versions import (
    SourceConfigVersionRepository,
    canonical_config_version,
)


def _source() -> dict[str, object]:
    return {
        "source_id": "source-x-openai",
        "platform": "x",
        "source_type": "creator",
        "identifier": "openai",
        "display_name": "OpenAI",
        "notes": "Official account",
        "cadence_hours": 6,
        "enabled": True,
        "connector_id": "opencli-x",
        "default_domain_id": "legacy-domain",
        "topics": ["ai"],
        "purpose": "monitor",
        "raw_metadata": {
            "max_items_per_run": 20,
            "auto_enrich_media": True,
            "command": ["twitter", "tweets", "openai", "--limit", "20"],
            "items_path": "data.items",
            "field_mapping": {"id": "id", "text": "text"},
            "metadata_mapping": {"likes": "public_metrics.like_count"},
            "detail_command": ["twitter", "tweet", "{url}"],
            "result_order": "newest_first",
            "requires_browser_session": True,
        },
    }


def _connect(db_path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma foreign_keys = on")
    return conn


def _seed_source(conn: sqlite3.Connection, source: dict[str, object]) -> None:
    conn.execute(
        """
        insert into sources (
          source_id, platform, source_type, identifier, display_name,
          cadence_hours, notes, enabled, connector_status, connector_id,
          raw_metadata, created_at, updated_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source["source_id"],
            source["platform"],
            source["source_type"],
            source["identifier"],
            source["display_name"],
            source["cadence_hours"],
            source["notes"],
            1 if source["enabled"] else 0,
            "implemented_uncalibrated",
            source["connector_id"],
            json.dumps(source["raw_metadata"], ensure_ascii=False),
            "2026-07-16T00:00:00Z",
            "2026-07-16T00:00:00Z",
        ),
    )


def test_new_database_appends_and_lists_versions_newest_first(tmp_path) -> None:
    db_path = migrate_database_path(tmp_path / "new.sqlite3")
    source = _source()
    with _connect(db_path) as conn:
        _seed_source(conn, source)
        first = SourceConfigVersionRepository.append_to_connection(
            conn,
            source,
            change_reason="created",
            created_at="2026-07-16T01:00:00Z",
        )
        renamed = {**source, "display_name": "OpenAI News"}
        second = SourceConfigVersionRepository.append_to_connection(
            conn,
            renamed,
            change_reason="display_changed",
            created_at="2026-07-16T02:00:00Z",
        )

    versions = SourceConfigVersionRepository(tmp_path, db_path=db_path).list_versions(
        str(source["source_id"]),
        limit=10,
    )

    assert [row["version_id"] for row in versions] == [second["version_id"], first["version_id"]]
    assert versions[0]["config_json"]["display_name"] == "OpenAI News"
    assert versions[0]["change_reason"] == "display_changed"


def test_legacy_database_upgrade_can_store_source_versions(tmp_path) -> None:
    db_path = tmp_path / "legacy.sqlite3"
    with _connect(db_path) as conn:
        conn.execute(
            """
            create table sources (
              source_id text primary key, platform text not null, source_type text not null,
              identifier text not null, display_name text not null, default_domain_id text references domains(domain_id),
              cadence_hours real not null, notes text, enabled integer not null default 1,
              connector_status text not null, connector_id text, raw_metadata text not null default '{}',
              next_run_at text, last_run_at text, created_at text not null, updated_at text not null
            )
            """
        )
    migrate_database_path(db_path)
    source = _source()
    with _connect(db_path) as conn:
        _seed_source(conn, source)
        stored = SourceConfigVersionRepository.append_to_connection(
            conn,
            source,
            change_reason="created",
            created_at="2026-07-16T01:00:00Z",
        )

    assert SourceConfigVersionRepository(tmp_path, db_path=db_path).list_versions(
        str(source["source_id"]), limit=1
    ) == [stored]


def test_repeated_append_is_idempotent_and_keeps_stable_version_id(tmp_path) -> None:
    db_path = migrate_database_path(tmp_path / "idempotent.sqlite3")
    source = _source()
    with _connect(db_path) as conn:
        _seed_source(conn, source)
        first = SourceConfigVersionRepository.append_to_connection(
            conn,
            source,
            change_reason="created",
            created_at="2026-07-16T01:00:00Z",
        )
        repeated = SourceConfigVersionRepository.append_to_connection(
            conn,
            deepcopy(source),
            change_reason="fetch_config_changed",
            created_at="2026-07-16T09:00:00Z",
        )
        count = conn.execute(
            "select count(*) from source_config_versions where source_id = ?",
            (source["source_id"],),
        ).fetchone()[0]

    assert repeated == first
    assert count == 1
    assert first["version_id"] == repeated["version_id"]
    assert repeated["change_reason"] == "created"
    assert repeated["created_at"] == "2026-07-16T01:00:00Z"


@pytest.mark.parametrize(
    ("location", "key", "changed_value"),
    [
        ("source", "display_name", "OpenAI News"),
        ("source", "notes", "Changed notes"),
        ("source", "cadence_hours", 24),
        ("source", "enabled", False),
        ("raw_metadata", "max_items_per_run", 50),
        ("raw_metadata", "auto_enrich_media", False),
        ("raw_metadata", "command", ["twitter", "tweets", "openai", "--limit", "50"]),
    ],
)
def test_every_saved_configuration_change_gets_a_new_config_version(
    location: str,
    key: str,
    changed_value: object,
) -> None:
    source = _source()
    changed = deepcopy(source)
    target = changed if location == "source" else changed["raw_metadata"]
    assert isinstance(target, dict)
    target[key] = changed_value

    assert canonical_config_version(changed) != canonical_config_version(source)


def test_non_feed_config_version_keeps_legacy_hash_and_ignores_feed_fields() -> None:
    source = _source()
    changed = deepcopy(source)
    metadata = changed["raw_metadata"]
    assert isinstance(metadata, dict)
    metadata.update(
        {
            "feed_provider": "generic",
            "feed_content_mode": "feed_only",
        }
    )

    expected = "sha256:19a237672de8dc75e9ed848c7f8cc46e56fc02c97c512c0916c5dfd61ab08a72"
    assert canonical_config_version(source) == expected
    assert canonical_config_version(changed) == expected


def test_rss_default_policy_is_omitted() -> None:
    source = {
        "source_id": "rss",
        "platform": "rss",
        "source_type": "feed",
        "identifier": "https://example.com/feed",
        "display_name": "RSS",
        "notes": None,
        "cadence_hours": 24,
        "enabled": True,
        "connector_id": "rss",
        "raw_metadata": {
            "max_items_per_run": 20,
            "auto_enrich_media": True,
        },
    }
    explicit_default = deepcopy(source)
    metadata = explicit_default["raw_metadata"]
    assert isinstance(metadata, dict)
    metadata.update(
        {
            "feed_provider": "generic",
            "feed_content_mode": "feed_preferred",
        }
    )

    legacy = "sha256:0921f257f9e71093b9734222a666113ca7145bca43914cab3e824434f6640386"
    assert canonical_config_version(source) == legacy
    assert canonical_config_version(explicit_default) == legacy


def test_display_schedule_and_activation_versions_share_the_fetch_fingerprint() -> None:
    source = _source()
    changed = deepcopy(source)
    changed.update(
        {
            "display_name": "OpenAI News",
            "notes": "Display-only update",
            "cadence_hours": 24,
            "enabled": False,
        }
    )

    assert canonical_config_version(changed) != canonical_config_version(source)
    assert source_fetch_input_version(changed) == source_fetch_input_version(source)


def test_config_json_excludes_credentials_readiness_and_legacy_knowledge_metadata(tmp_path) -> None:
    db_path = migrate_database_path(tmp_path / "safe.sqlite3")
    source = _source()
    source.update(
        {
            "token": "top-secret-token",
            "cookie": "top-secret-cookie",
            "readiness": {"status": "ready"},
        }
    )
    raw_metadata = source["raw_metadata"]
    assert isinstance(raw_metadata, dict)
    raw_metadata.update(
        {
            "access_token": "nested-secret",
            "cookies": {"session": "nested-secret"},
            "browser_readiness": {"status": "ready"},
            "topics": ["legacy-topic"],
            "purpose": "legacy-purpose",
        }
    )
    with _connect(db_path) as conn:
        _seed_source(conn, source)
        stored = SourceConfigVersionRepository.append_to_connection(
            conn,
            source,
            change_reason="created",
            created_at="2026-07-16T01:00:00Z",
        )

    serialized = json.dumps(stored["config_json"], ensure_ascii=False, sort_keys=True)
    assert "secret" not in serialized
    assert "cookie" not in serialized
    assert "token" not in serialized
    assert "readiness" not in serialized
    assert "default_domain" not in serialized
    assert "legacy-domain" not in serialized
    assert "topics" not in serialized
    assert "purpose" not in serialized


def test_append_uses_the_callers_transaction_without_committing(tmp_path) -> None:
    db_path = migrate_database_path(tmp_path / "transaction.sqlite3")
    source = _source()
    with _connect(db_path) as conn:
        _seed_source(conn, source)
    conn = _connect(db_path)
    try:
        SourceConfigVersionRepository.append_to_connection(
            conn,
            source,
            change_reason="created",
            created_at="2026-07-16T01:00:00Z",
        )
        conn.rollback()
    finally:
        conn.close()

    assert SourceConfigVersionRepository(tmp_path, db_path=db_path).list_versions(
        str(source["source_id"]), limit=10
    ) == []


def test_list_versions_applies_limit(tmp_path) -> None:
    db_path = migrate_database_path(tmp_path / "limit.sqlite3")
    source = _source()
    with _connect(db_path) as conn:
        _seed_source(conn, source)
        for index in range(3):
            changed = {**source, "display_name": f"OpenAI {index}"}
            SourceConfigVersionRepository.append_to_connection(
                conn,
                changed,
                change_reason="display_changed",
                created_at=f"2026-07-16T0{index + 1}:00:00Z",
            )

    versions = SourceConfigVersionRepository(tmp_path, db_path=db_path).list_versions(
        str(source["source_id"]), limit=2
    )

    assert [row["config_json"]["display_name"] for row in versions] == ["OpenAI 2", "OpenAI 1"]
