def test_get_config_creates_default_if_missing(tmp_path, monkeypatch):
    from pathlib import Path
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    from backend.infrastructure.notifications.rules import get_config
    monkeypatch.setattr("backend.infrastructure.notifications.rules.CONFIG_DIR", config_dir)
    monkeypatch.setattr("backend.infrastructure.notifications.rules.DEFAULT_CONFIG_PATH", config_dir / "notifications.json")
    config = get_config()
    assert config["enabled"] is False
    assert config["email"]["smtp_host"] == "smtp.gmail.com"


def test_is_due_and_record_sent(tmp_path):
    from backend.infrastructure.notifications.rules import is_due, record_sent
    # First time should be due
    assert is_due("test_rule", 60, tmp_path) is True
    record_sent("test_rule", tmp_path)
    # Immediately after should not be due
    assert is_due("test_rule", 60, tmp_path) is False


def test_format_high_priority_email():
    from backend.infrastructure.notifications.templates import format_high_priority_email
    cards = [{"title": "Starship launch", "source_platform": "x", "source_url": "https://x.com/status/1", "scores": {"timeliness": 5, "insight": 4}}]
    subject, body = format_high_priority_email(cards)
    assert "Starship" in subject
    assert "High-Priority" in body


def test_notification_value_object_remains_available_without_jsonl_store():
    from backend.infrastructure.notifications import NotificationRecord
    assert NotificationRecord is not None


def test_check_and_notify_enabled_email_uses_base_path(tmp_path, monkeypatch):
    from backend.infrastructure import notifications

    config = {"enabled": True, "email": {"username": "test@example.com"}}
    sent = []
    recorded = []
    monkeypatch.setattr(notifications, "get_config", lambda: config)
    monkeypatch.setattr(notifications, "is_due", lambda *args: True)
    monkeypatch.setattr(notifications, "send_email", lambda **kwargs: sent.append(kwargs) or True)
    monkeypatch.setattr(notifications, "record_sent", lambda rule, path: recorded.append((rule, path)))

    result = notifications.check_and_notify(
        str(tmp_path), cards=[{"title": "Test", "scores": {"timeliness": 5}}],
    )

    assert result == {"status": "processed", "results": [{"type": "high_priority", "cards": 1, "sent": True}]}
    assert sent[0]["config"] is config
    assert recorded == [("high_priority", tmp_path)]
