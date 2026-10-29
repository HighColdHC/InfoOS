from __future__ import annotations

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def test_environment_paths_default_to_installed_user_state(monkeypatch):
    from backend.infrastructure.runtime import environment

    monkeypatch.delenv("INFOOS_STATE_HOME", raising=False)
    monkeypatch.delenv("INFOOS_CHROME_PROFILE_DIR", raising=False)
    monkeypatch.delenv("INFOOS_BROWSER_SESSION_JSON", raising=False)
    monkeypatch.delenv("INFOOS_BROWSER_SESSION_LOCK", raising=False)
    monkeypatch.delenv("INFOOS_CONFIG_PATH", raising=False)
    monkeypatch.delenv("INFOOS_CONFIG_DIR", raising=False)
    monkeypatch.delenv("INFOOS_NOTIFICATIONS_CONFIG", raising=False)

    assert environment.state_home() == Path.home() / ".infoos"
    assert environment.chrome_profile_dir() == Path.home() / ".infoos" / "chrome-profile"
    assert environment.browser_session_json_path() == Path.home() / ".infoos" / "browser-session.json"
    assert environment.browser_session_lock_path() == Path.home() / ".infoos" / "browser-session.json.lock"
    assert environment.config_yaml_path() == Path.home() / ".infoos" / "config.yaml"
    assert environment.config_dir() == Path.home() / ".infoos" / "config"
    assert environment.notifications_config_path() == Path.home() / ".infoos" / "config" / "notifications.json"
    assert environment.setup_state_path() == Path.home() / ".infoos" / "setup-state.json"


def test_environment_paths_can_isolate_live_dev_state(monkeypatch, tmp_path):
    from backend.infrastructure.runtime import environment

    state_home = tmp_path / ".infoos-dev"
    monkeypatch.setenv("INFOOS_STATE_HOME", str(state_home))

    assert environment.state_home() == state_home
    assert environment.chrome_profile_dir() == state_home / "chrome-profile"
    assert environment.browser_session_json_path() == state_home / "browser-session.json"
    assert environment.browser_session_lock_path() == state_home / "browser-session.json.lock"
    assert environment.config_yaml_path() == state_home / "config.yaml"
    assert environment.config_dir() == state_home / "config"
    assert environment.notifications_config_path() == state_home / "config" / "notifications.json"
    assert environment.setup_state_path() == state_home / "setup-state.json"


def test_specific_environment_paths_override_state_home(monkeypatch, tmp_path):
    from backend.infrastructure.runtime import environment

    monkeypatch.setenv("INFOOS_STATE_HOME", str(tmp_path / ".infoos-dev"))
    monkeypatch.setenv("INFOOS_CHROME_PROFILE_DIR", str(tmp_path / "chrome"))
    monkeypatch.setenv("INFOOS_BROWSER_SESSION_JSON", str(tmp_path / "session.json"))
    monkeypatch.setenv("INFOOS_BROWSER_SESSION_LOCK", str(tmp_path / "session.lock"))
    monkeypatch.setenv("INFOOS_CONFIG_PATH", str(tmp_path / "config.yaml"))
    monkeypatch.setenv("INFOOS_CONFIG_DIR", str(tmp_path / "config-dir"))
    monkeypatch.setenv("INFOOS_NOTIFICATIONS_CONFIG", str(tmp_path / "notifications.json"))

    assert environment.chrome_profile_dir() == tmp_path / "chrome"
    assert environment.browser_session_json_path() == tmp_path / "session.json"
    assert environment.browser_session_lock_path() == tmp_path / "session.lock"
    assert environment.config_yaml_path() == tmp_path / "config.yaml"
    assert environment.config_dir() == tmp_path / "config-dir"
    assert environment.notifications_config_path() == tmp_path / "notifications.json"


def test_browser_console_url_is_same_origin_and_rejects_external_values(monkeypatch):
    from backend.infrastructure.runtime import environment

    monkeypatch.setenv("INFOOS_BROWSER_CONSOLE_URL", "/browser-console/")
    assert environment.browser_console_url() == "/browser-console/"

    monkeypatch.setenv("INFOOS_BROWSER_CONSOLE_URL", "https://example.com/console")
    assert environment.browser_console_url() == "/browser-console/"

    monkeypatch.setenv("INFOOS_BROWSER_CONSOLE_URL", "//example.com/console")
    assert environment.browser_console_url() == "/browser-console/"
