from __future__ import annotations

from pathlib import Path

from backend.infrastructure.security.redaction import REDACTED, redact_secrets, redact_text


def test_redaction_covers_nested_keys_and_inline_credentials(monkeypatch, tmp_path: Path) -> None:
    state_home = tmp_path / "state"
    data_home = tmp_path / "data"
    monkeypatch.setenv("INFOOS_STATE_HOME", str(state_home))
    monkeypatch.setenv("INFOOS_DATA_HOME", str(data_home))
    plaintext = "ios_abcdefghijklmnopqrstuvwxyz0123456789"
    value = {
        "authorization": f"Bearer {plaintext}",
        "nested": [
            f"Authorization: Bearer {plaintext}",
            "Cookie: session=secret-value",
            "https://example.test/path?api_key=super-secret&safe=1",
            f"wrote {data_home}/exports/file.zip",
        ],
    }

    redacted = redact_secrets(value)

    assert redacted["authorization"] == REDACTED
    encoded = str(redacted)
    assert plaintext not in encoded
    assert "secret-value" not in encoded
    assert "super-secret" not in encoded
    assert str(data_home) not in encoded
    assert "<INFOOS_DATA_HOME>" in encoded


def test_redaction_keeps_safe_masked_token_prefix() -> None:
    assert redact_secrets("ios_abcd1234…") == "ios_abcd1234…"


def test_redaction_updates_cached_roots_when_environment_changes(monkeypatch, tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    monkeypatch.setenv("INFOOS_DATA_HOME", str(first))
    assert redact_text(f"{first}/file") == "<INFOOS_DATA_HOME>/file"
    monkeypatch.setenv("INFOOS_DATA_HOME", str(second))
    assert redact_text(f"{second}/file") == "<INFOOS_DATA_HOME>/file"
