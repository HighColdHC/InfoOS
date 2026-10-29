from __future__ import annotations

from hashlib import sha256
import sqlite3

import pytest

from backend.infrastructure.storage.api_tokens import ApiTokenRepository
from backend.infrastructure.storage.bootstrap import initialize_runtime_storage
from backend.infrastructure.storage.database import connect_writable


def _digest(plaintext: str) -> str:
    return sha256(plaintext.encode("utf-8")).hexdigest()


def _append_token(
    repo: ApiTokenRepository,
    *,
    token_id: str = "token-1",
    plaintext: str = "ios_plaintext-that-must-never-be-stored",
    created_at: str = "2026-07-16T01:00:00Z",
) -> dict[str, object]:
    return repo.append(
        token_id=token_id,
        name="Obsidian",
        token_prefix="ios_plaintex…",
        token_digest=_digest(plaintext),
        scopes=["assets:read", "cards:read", "cards:read"],
        created_at=created_at,
    )


def test_append_hash_lookup_and_management_projection_never_expose_plaintext_or_digest(tmp_path) -> None:
    repo = ApiTokenRepository(tmp_path)
    plaintext = "ios_plaintext-that-must-never-be-stored"

    stored = _append_token(repo, plaintext=plaintext)

    internal = repo.find_by_digest(_digest(plaintext))
    management = repo.list_tokens(limit=10)
    assert internal == stored
    assert internal is not None
    assert internal["scopes"] == ["assets:read", "cards:read"]
    assert management == [
        {
            "token_id": "token-1",
            "name": "Obsidian",
            "token_prefix": "ios_plaintex…",
            "scopes": ["assets:read", "cards:read"],
            "created_at": "2026-07-16T01:00:00Z",
            "revoked_at": None,
            "last_used_at": None,
            "replaced_by_token_id": None,
            "webhook_enabled": False,
            "webhook_include_backfill": False,
            "webhook_url": None,
            "webhook_config_generation": 1,
            "webhook_delivery_status": "inactive",
            "webhook_consecutive_failures": 0,
            "webhook_last_attempt_at": None,
            "webhook_last_success_at": None,
            "webhook_last_failure_at": None,
            "webhook_last_error": None,
            "webhook_circuit_opened_at": None,
            "updated_at": "2026-07-16T01:00:00Z",
        }
    ]
    assert "token_digest" not in management[0]
    with sqlite3.connect(repo.db_path) as conn:
        database_text = "\n".join(str(value) for row in conn.execute("select * from api_tokens") for value in row)
    assert plaintext not in database_text


def test_repository_signature_rejects_plaintext_token(tmp_path) -> None:
    repo = ApiTokenRepository(tmp_path)

    with pytest.raises(TypeError, match="plaintext_token"):
        repo.append(
            token_id="token-unsafe",
            name="Unsafe",
            token_prefix="ios_unsafe…",
            token_digest=_digest("safe-digest-input"),
            scopes=["cards:read"],
            plaintext_token="ios_must-not-be-accepted",
        )


def test_revoke_last_used_and_replacement_updates_are_idempotent(tmp_path) -> None:
    repo = ApiTokenRepository(tmp_path)
    _append_token(repo, token_id="token-old")
    _append_token(
        repo,
        token_id="token-new",
        plaintext="ios_replacement-plaintext",
        created_at="2026-07-16T02:00:00Z",
    )

    repo.update_last_used("token-old", used_at="2026-07-16T03:00:00Z")
    replaced = repo.mark_replaced(
        "token-old",
        replaced_by_token_id="token-new",
        revoked_at="2026-07-16T04:00:00Z",
    )
    repeated = repo.revoke("token-old", revoked_at="2026-07-16T09:00:00Z")

    assert replaced is not None
    assert repeated is not None
    assert repeated["last_used_at"] == "2026-07-16T03:00:00Z"
    assert repeated["revoked_at"] == "2026-07-16T04:00:00Z"
    assert repeated["replaced_by_token_id"] == "token-new"
    assert repo.revoke("missing", revoked_at="2026-07-16T05:00:00Z") is None
    assert repo.update_last_used("missing", used_at="2026-07-16T05:00:00Z") is None


def test_create_to_connection_uses_callers_transaction_without_committing(tmp_path) -> None:
    repo = ApiTokenRepository(tmp_path)
    conn = connect_writable(repo.db_path)
    try:
        ApiTokenRepository.create_to_connection(
            conn,
            token_id="token-transaction",
            name="Transaction",
            token_prefix="ios_transact…",
            token_digest=_digest("ios_transaction-plaintext"),
            scopes=["cards:read"],
            created_at="2026-07-16T01:00:00Z",
        )
        assert conn.in_transaction is True
        conn.rollback()
    finally:
        conn.close()

    assert repo.get_internal("token-transaction") is None


def test_old_database_upgrade_and_repeated_initialization_create_token_tables(tmp_path) -> None:
    base_dir = tmp_path / "legacy"
    db_path = base_dir / "data" / "infoos.sqlite3"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("create table legacy_marker (value text not null)")
        conn.execute("insert into legacy_marker values ('preserve-me')")

    initialize_runtime_storage(base_dir)
    initialize_runtime_storage(base_dir)

    repo = ApiTokenRepository(base_dir)
    _append_token(repo, token_id="token-after-upgrade")
    with sqlite3.connect(db_path) as conn:
        tables = {row[0] for row in conn.execute("select name from sqlite_master where type='table'")}
        token_columns = {row[1] for row in conn.execute("pragma table_info(api_tokens)")}
        marker = conn.execute("select value from legacy_marker").fetchone()[0]
    assert {"api_tokens", "api_request_logs"} <= tables
    assert "config_revision" in token_columns
    assert marker == "preserve-me"
    assert {
        "webhook_enabled",
        "webhook_url",
        "webhook_delivery_status",
        "webhook_consecutive_failures",
        "webhook_last_attempt_at",
        "webhook_last_success_at",
        "webhook_last_failure_at",
        "webhook_last_error",
        "webhook_circuit_opened_at",
        "updated_at",
    } <= token_columns
    assert repo.get_internal("token-after-upgrade") is not None
