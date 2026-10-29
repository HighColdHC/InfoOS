"""Framework-neutral Bearer authentication for the plugin read interface."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import hmac
from pathlib import Path

from backend.infrastructure.storage.api_tokens import ApiTokenRepository


INTELLIGENCE_READ = "intelligence:read"
EVALUATION_WRITE = "evaluation:write"
PROFILE_WRITE = "profile:write"
FEEDBACK_WRITE = "feedback:write"
COLLECT_WRITE = "collect:write"
SOURCES_WRITE = "sources:write"
VERIFICATION_WRITE = "verification:write"


@dataclass(frozen=True, slots=True)
class AuthContext:
    token_id: str
    name: str
    token_prefix: str
    scopes: frozenset[str]


class PluginAuthError(PermissionError):
    """Stable, credential-free authentication failure."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def authenticate_bearer(
    base_dir: Path | str,
    authorization: str | None,
    *,
    required_scope: str | None,
) -> AuthContext:
    plaintext = _parse_bearer(authorization)
    return authenticate_token(base_dir, plaintext, required_scope=required_scope)


def authenticate_token(
    base_dir: Path | str,
    plaintext_token: str,
    *,
    required_scope: str | None,
) -> AuthContext:
    candidate_digest = sha256(plaintext_token.encode("utf-8")).hexdigest()
    stored = ApiTokenRepository(base_dir).find_by_digest(candidate_digest)
    if stored is None or not hmac.compare_digest(
        str(stored["token_digest"]),
        candidate_digest,
    ):
        raise PluginAuthError("invalid_token", "Invalid API token")
    if stored.get("revoked_at"):
        raise PluginAuthError("token_revoked", "API token has been revoked")
    scopes = frozenset(str(scope) for scope in stored.get("scopes", []))
    if required_scope is not None and required_scope not in scopes:
        raise PluginAuthError("insufficient_scope", f"API token requires scope {required_scope}")
    return AuthContext(
        token_id=str(stored["token_id"]),
        name=str(stored["name"]),
        token_prefix=str(stored["token_prefix"]),
        scopes=scopes,
    )


def _parse_bearer(authorization: str | None) -> str:
    if authorization is None or not authorization.strip():
        raise PluginAuthError("missing_token", "Bearer token is required")
    parts = authorization.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
        raise PluginAuthError("invalid_token", "Invalid Bearer authorization")
    return parts[1]
