from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
LAUNCHER = REPO_ROOT / "scripts" / "opencli-infoos-chrome"
TEST_ENV = REPO_ROOT / "scripts" / "run-infoos-test-env.sh"


def test_collector_uses_stable_chrome_with_a_dedicated_profile() -> None:
    source = LAUNCHER.read_text()

    assert '"/Applications/Google Chrome.app"' in source
    assert "/usr/bin/google-chrome-stable" in source
    assert "Google Chrome for Testing" not in source
    assert 'PROFILE_DIR="${INFOOS_CHROME_PROFILE_DIR:-${STATE_HOME}/chrome-profile}"' in source
    assert '--user-data-dir="$PROFILE_DIR"' in source


def test_stable_chrome_launcher_has_no_remote_automation_switches() -> None:
    source = LAUNCHER.read_text()

    assert "--no-sandbox" not in source
    assert '--remote-debugging-port' not in source
    assert 'prepare-infoos-opencli-bridge.mjs' not in source
    assert '--load-extension="$OPENCLI_EXTENSION_DIR"' not in source


def test_test_environment_keeps_its_profile_outside_the_default_environment() -> None:
    source = TEST_ENV.read_text()

    assert 'TEST_ROOT="${INFOOS_TEST_ROOT:-$EXPECTED_TEST_ROOT}"' in source
    assert 'TEST_STATE_HOME="${INFOOS_TEST_STATE_HOME:-$TEST_ROOT}"' in source
    assert 'TEST_CHROME_PROFILE="$TEST_STATE_HOME/chrome-profile"' in source
    assert 'export INFOOS_CHROME_PROFILE_DIR="$TEST_CHROME_PROFILE"' in source
