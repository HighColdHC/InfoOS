from __future__ import annotations

import pytest

from backend.application.usecases.api_tokens import create_token, revoke_token, rotate_token
from backend.infrastructure.security import plugin_auth
from backend.infrastructure.security.plugin_auth import (
    AuthContext,
    PluginAuthError,
    authenticate_bearer,
)


def _created(tmp_path, scopes: list[str] | None = None) -> dict:
    return create_token(tmp_path, name="Obsidian", scopes=scopes or ["cards:read"])


@pytest.mark.parametrize(
    ("authorization", "code"),
    [
        (None, "missing_token"),
        ("", "missing_token"),
        ("Basic abc", "invalid_token"),
        ("Bearer", "invalid_token"),
        ("Bearer definitely-wrong", "invalid_token"),
    ],
)
def test_missing_malformed_and_unknown_tokens_have_stable_safe_errors(
    tmp_path,
    authorization: str | None,
    code: str,
) -> None:
    with pytest.raises(PluginAuthError) as captured:
        authenticate_bearer(
            tmp_path,
            authorization,
            required_scope="cards:read",
        )

    assert captured.value.code == code
    assert "definitely-wrong" not in str(captured.value)
    assert "definitely-wrong" not in repr(captured.value)


def test_valid_token_returns_safe_auth_context_and_uses_constant_time_compare(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _created(tmp_path, ["cards:read", "assets:read"])
    plaintext = created["plaintext_token"]
    compared: list[tuple[str, str]] = []
    real_compare = plugin_auth.hmac.compare_digest

    def record_compare(left: str, right: str) -> bool:
        compared.append((left, right))
        return real_compare(left, right)

    monkeypatch.setattr(plugin_auth.hmac, "compare_digest", record_compare)

    context = authenticate_bearer(
        tmp_path,
        f"Bearer {plaintext}",
        required_scope="assets:read",
    )

    assert isinstance(context, AuthContext)
    assert context.token_id == created["token"]["token_id"]
    assert context.token_prefix == created["token"]["token_prefix"]
    assert context.scopes == frozenset({"cards:read", "assets:read"})
    assert compared and compared[0][0] == compared[0][1]
    assert plaintext not in repr(context)
    assert not hasattr(context, "token_digest")


def test_revoked_token_and_insufficient_scope_have_distinct_errors(tmp_path) -> None:
    cards_only = _created(tmp_path, ["cards:read"])
    with pytest.raises(PluginAuthError) as insufficient:
        authenticate_bearer(
            tmp_path,
            f"Bearer {cards_only['plaintext_token']}",
            required_scope="assets:read",
        )
    assert insufficient.value.code == "insufficient_scope"

    revoke_token(tmp_path, cards_only["token"]["token_id"])
    with pytest.raises(PluginAuthError) as revoked:
        authenticate_bearer(
            tmp_path,
            f"Bearer {cards_only['plaintext_token']}",
            required_scope="cards:read",
        )
    assert revoked.value.code == "token_revoked"
    assert cards_only["plaintext_token"] not in repr(revoked.value)


def test_rotation_rejects_old_plaintext_and_accepts_new_plaintext(tmp_path) -> None:
    old = _created(tmp_path, ["cards:read"])
    rotated = rotate_token(tmp_path, old["token"]["token_id"])

    with pytest.raises(PluginAuthError) as old_error:
        authenticate_bearer(
            tmp_path,
            f"Bearer {old['plaintext_token']}",
            required_scope="cards:read",
        )
    accepted = authenticate_bearer(
        tmp_path,
        f"Bearer {rotated['plaintext_token']}",
        required_scope="cards:read",
    )

    assert old_error.value.code == "token_revoked"
    assert accepted.token_id == rotated["token"]["token_id"]
