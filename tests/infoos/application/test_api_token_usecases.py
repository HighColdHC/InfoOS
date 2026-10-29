from __future__ import annotations

from hashlib import sha256
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading
from uuid import UUID

import pytest

from backend.application.usecases import api_tokens as token_usecases
from backend.application.usecases.api_tokens import (
    create_token,
    ApiTokenConfigurationChangedError,
    revoke_token,
    rotate_token,
    update_token_webhook,
)
from backend.infrastructure.storage.api_tokens import ApiTokenRepository


def test_create_returns_plaintext_once_but_only_digest_reaches_storage(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_body = "A" * 43
    monkeypatch.setattr(token_usecases.secrets, "token_urlsafe", lambda size: token_body)

    result = create_token(
        tmp_path,
        name=" Obsidian Readonly ",
        scopes=["cards:read", "assets:read", "cards:read"],
        now="2026-07-16T01:00:00Z",
    )

    plaintext = f"ios_{token_body}"
    assert result["plaintext_token"] == plaintext
    assert result["token"]["name"] == "Obsidian Readonly"
    assert result["token"]["token_prefix"] == f"{plaintext[:12]}…"
    assert result["token"]["scopes"] == ["assets:read", "cards:read"]
    UUID(result["token"]["token_id"])
    assert "token_digest" not in result["token"]

    internal = ApiTokenRepository(tmp_path).get_internal(result["token"]["token_id"])
    assert internal is not None
    assert internal["token_digest"] == sha256(plaintext.encode("utf-8")).hexdigest()
    with sqlite3.connect(ApiTokenRepository(tmp_path).db_path) as conn:
        database_text = "\n".join(str(value) for row in conn.execute("select * from api_tokens") for value in row)
    assert plaintext not in database_text


@pytest.mark.parametrize("scopes", [[], ["events:read"], ["cards:read", "admin"]])
def test_create_rejects_empty_or_unknown_scopes(tmp_path, scopes: list[str]) -> None:
    with pytest.raises(ValueError, match="scope"):
        create_token(tmp_path, name="Invalid", scopes=scopes)

    assert ApiTokenRepository(tmp_path).list_tokens(limit=10) == []


def test_revoke_is_idempotent_and_never_returns_digest(tmp_path) -> None:
    created = create_token(tmp_path, name="Obsidian", scopes=["cards:read"])
    token_id = created["token"]["token_id"]

    first = revoke_token(tmp_path, token_id, now="2026-07-16T02:00:00Z")
    second = revoke_token(tmp_path, token_id, now="2026-07-16T09:00:00Z")

    assert first == second
    assert first["revoked_at"] == "2026-07-16T02:00:00Z"
    assert "token_digest" not in first
    assert revoke_token(tmp_path, "missing", now="2026-07-16T03:00:00Z") is None


def test_rotate_creates_new_token_and_revokes_old_in_one_transaction(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bodies = iter(("O" * 43, "N" * 43))
    monkeypatch.setattr(token_usecases.secrets, "token_urlsafe", lambda size: next(bodies))
    old = create_token(
        tmp_path,
        name="Obsidian",
        scopes=["cards:read", "assets:read"],
        now="2026-07-16T01:00:00Z",
    )

    rotated = rotate_token(
        tmp_path,
        old["token"]["token_id"],
        now="2026-07-16T02:00:00Z",
    )

    assert rotated["plaintext_token"] == f"ios_{'N' * 43}"
    assert rotated["token"]["token_id"] != old["token"]["token_id"]
    assert rotated["token"]["scopes"] == ["assets:read", "cards:read"]
    old_internal = ApiTokenRepository(tmp_path).get_internal(old["token"]["token_id"])
    assert old_internal["revoked_at"] == "2026-07-16T02:00:00Z"
    assert old_internal["replaced_by_token_id"] == rotated["token"]["token_id"]
    assert rotated["old_token"]["token_id"] == old["token"]["token_id"]
    assert "plaintext_token" not in rotated["old_token"]


def test_rotate_rolls_back_new_token_when_old_token_update_fails(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = create_token(tmp_path, name="Obsidian", scopes=["cards:read"])
    before = ApiTokenRepository(tmp_path).list_tokens(limit=10)

    def fail_replace(*_args, **_kwargs):
        raise sqlite3.IntegrityError("forced replacement failure")

    monkeypatch.setattr(ApiTokenRepository, "replace_to_connection", fail_replace)

    with pytest.raises(sqlite3.IntegrityError, match="forced replacement failure"):
        rotate_token(
            tmp_path,
            old["token"]["token_id"],
            now="2026-07-16T02:00:00Z",
        )

    assert ApiTokenRepository(tmp_path).list_tokens(limit=10) == before
    assert ApiTokenRepository(tmp_path).get_internal(old["token"]["token_id"])["revoked_at"] is None


def test_metadata_only_token_update_preserves_webhook_generation(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = create_token(
        tmp_path,
        name="Before",
        scopes=["cards:read"],
        webhook_enabled=True,
        webhook_url="http://127.0.0.1:27124/hook",
    )["token"]
    token_repo = ApiTokenRepository(tmp_path)
    for index in range(3):
        token_repo.record_webhook_failure(
            token["token_id"],
            error=f"failure-{index}",
            attempted_at=f"2026-07-20T00:0{index}:00Z",
        )
    before = token_repo.get_internal(token["token_id"])
    monkeypatch.setattr(
        token_usecases,
        "_probe_or_raise",
        lambda *_args, **_kwargs: pytest.fail("unchanged webhook must not be probed"),
    )

    updated = update_token_webhook(
        tmp_path,
        token["token_id"],
        name="After",
        scopes=["cards:read", "assets:read"],
        webhook_enabled=True,
        webhook_url="http://127.0.0.1:27124/hook",
        verify_webhook=True,
    )

    assert updated["webhook_config_generation"] == token["webhook_config_generation"]
    after = token_repo.get_internal(token["token_id"])
    assert after["webhook_delivery_status"] == "paused"
    assert after["webhook_consecutive_failures"] == 3
    assert after["webhook_last_error"] == before["webhook_last_error"]
    assert after["webhook_last_failure_at"] == before["webhook_last_failure_at"]
    assert after["config_revision"] == before["config_revision"] + 1


def test_rotation_loses_cas_when_metadata_changes_after_read(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = create_token(tmp_path, name="Before", scopes=["cards:read"])["token"]
    real_get = ApiTokenRepository.get_internal
    changed = False

    def update_after_read(repository, token_id):
        nonlocal changed
        value = real_get(repository, token_id)
        if token_id == token["token_id"] and not changed:
            changed = True
            updated = repository.update_configuration(
                token_id,
                name="Concurrent",
                scopes=["cards:read", "assets:read"],
                webhook_enabled=False,
                webhook_url=None,
                expected_revision=int(value["config_revision"]),
                reset_webhook_health=False,
            )
            assert updated is not None
        return value

    monkeypatch.setattr(ApiTokenRepository, "get_internal", update_after_read)

    with pytest.raises(ApiTokenConfigurationChangedError):
        rotate_token(tmp_path, token["token_id"])

    current = real_get(ApiTokenRepository(tmp_path), token["token_id"])
    assert current["name"] == "Concurrent"
    assert current["scopes"] == ["assets:read", "cards:read"]
    assert current["revoked_at"] is None
    assert len(ApiTokenRepository(tmp_path).list_tokens(limit=10)) == 1


def test_webhook_edit_loses_cas_when_token_rotates_during_probe(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = create_token(
        tmp_path,
        name="Race",
        scopes=["cards:read"],
        webhook_enabled=True,
        webhook_url="http://127.0.0.1:27124/old",
    )["token"]
    replacement: dict = {}

    def rotate_during_probe(*_args, **_kwargs):
        replacement.update(rotate_token(tmp_path, old["token_id"])["token"])
        return 204

    monkeypatch.setattr(token_usecases, "_probe_or_raise", rotate_during_probe)

    with pytest.raises(ApiTokenConfigurationChangedError):
        update_token_webhook(
            tmp_path,
            old["token_id"],
            webhook_enabled=True,
            webhook_url="http://127.0.0.1:27125/new",
            verify_webhook=True,
        )

    old_state = ApiTokenRepository(tmp_path).get_internal(old["token_id"])
    new_state = ApiTokenRepository(tmp_path).get_internal(replacement["token_id"])
    assert old_state["webhook_url"].endswith("/old")
    assert new_state["webhook_url"].endswith("/old")


def test_two_concurrent_rotations_leave_exactly_one_replacement(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = create_token(tmp_path, name="Race", scopes=["cards:read"])["token"]
    barrier = threading.Barrier(2)
    real_get = ApiTokenRepository.get_internal

    def synchronized_get(repository, token_id):
        value = real_get(repository, token_id)
        if token_id == old["token_id"]:
            barrier.wait(timeout=5)
        return value

    monkeypatch.setattr(ApiTokenRepository, "get_internal", synchronized_get)

    def rotate_once():
        try:
            return rotate_token(tmp_path, old["token_id"])["token"]["token_id"]
        except ApiTokenConfigurationChangedError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: rotate_once(), range(2)))

    assert len([token_id for token_id in results if token_id]) == 1
    tokens = ApiTokenRepository(tmp_path).list_tokens(limit=10)
    assert len(tokens) == 2
    assert sum(token["revoked_at"] is None for token in tokens) == 1


def test_two_concurrent_metadata_updates_compare_and_swap_revision(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = create_token(tmp_path, name="Before", scopes=["cards:read"])["token"]
    barrier = threading.Barrier(2)
    real_get = ApiTokenRepository.get_internal

    def synchronized_get(repository, token_id):
        value = real_get(repository, token_id)
        if token_id == token["token_id"]:
            barrier.wait(timeout=5)
        return value

    monkeypatch.setattr(ApiTokenRepository, "get_internal", synchronized_get)

    def update_once(index: int):
        try:
            return update_token_webhook(
                tmp_path,
                token["token_id"],
                name=f"Name {index}",
                webhook_enabled=False,
                webhook_url=None,
            )["name"]
        except ApiTokenConfigurationChangedError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(update_once, range(2)))

    assert len([name for name in results if name]) == 1
    final_state = real_get(ApiTokenRepository(tmp_path), token["token_id"])
    assert final_state["config_revision"] == 2
