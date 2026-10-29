"""Tests for backend.infrastructure.browser.opencli_health."""
import subprocess
from unittest.mock import patch, MagicMock

import pytest

from backend.infrastructure.browser.opencli_health import (
    parse_doctor_output,
    parse_profile_list_output,
    refresh,
)


HEALTHY_DOCTOR = """opencli v1.8.0 doctor (node v24.15.0)

[OK] Daemon: running on port 19825 (v1.8.0)
[OK] Extension: connected (v1.0.2) → v1.0.19 available

Profiles:
  • default: connected v1.0.2
[OK] Connectivity: connected in 0.1s

Issues:
  • Extension update available: v1.0.2 → v1.0.19
"""


EXT_DISCONNECTED_DOCTOR = """opencli v1.8.0 doctor (node v24.15.0)

[FAIL] Daemon: not running
[FAIL] Extension: not connected
[FAIL] Connectivity: timeout after 30s
"""


DAEMON_DOWN_DOCTOR = """opencli: command not found
"""


PROFILE_LIST_OUTPUT = """Connected Browser Bridge profiles

  default — connected v1.0.2
  custom-1 — connected v1.0.2
"""

DAEMON_STATUS_OUTPUT = """Daemon: running (PID 1840)
Version: v1.8.6
Uptime: 1h 6m
Extension: connected (v1.0.22)
Profiles: fudrbrty v1.0.22
Memory: 73.6 MB
Port: 19825
"""


def test_parse_doctor_output_healthy():
    result = parse_doctor_output(HEALTHY_DOCTOR)
    assert result["daemon_running"] is True
    assert result["daemon_version"] == "1.8.0"
    assert result["extension_connected"] is True
    assert result["doctor_status"] == "ok"


def test_parse_doctor_output_extension_disconnected():
    result = parse_doctor_output(EXT_DISCONNECTED_DOCTOR)
    assert result["daemon_running"] is False
    assert result["extension_connected"] is False
    assert result["doctor_status"] == "failed"


def test_parse_doctor_output_daemon_down():
    result = parse_doctor_output(DAEMON_DOWN_DOCTOR)
    assert result["daemon_running"] is False
    assert result["extension_connected"] is False
    assert result["doctor_status"] == "failed"


def test_parse_profile_list_output():
    result = parse_profile_list_output(PROFILE_LIST_OUTPUT)
    assert len(result) == 2
    assert result[0]["alias"] == "default"
    assert result[0]["status"] == "connected"
    assert result[1]["alias"] == "custom-1"


@patch("backend.infrastructure.browser.opencli_health.subprocess.run")
def test_refresh_returns_combined_payload(mock_run):
    proc1 = MagicMock(returncode=0, stdout=DAEMON_STATUS_OUTPUT, stderr="")
    proc2 = MagicMock(returncode=0, stdout=PROFILE_LIST_OUTPUT, stderr="")
    mock_run.side_effect = [proc1, proc2]
    result = refresh()
    assert result["daemon_running"] is True
    assert result["daemon_version"] == "1.8.6"
    assert result["extension_connected"] is True
    assert len(result["profiles"]) == 2
    assert "last_checked_at" in result
    assert all("doctor" not in call.args[0] for call in mock_run.call_args_list)


@patch("backend.infrastructure.browser.opencli_health.subprocess.run")
def test_refresh_uses_configured_opencli_executable(mock_run, monkeypatch):
    monkeypatch.setenv("INFOOS_OPENCLI_EXECUTABLE", "/managed/opencli")
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout=DAEMON_STATUS_OUTPUT, stderr=""),
        MagicMock(returncode=0, stdout=PROFILE_LIST_OUTPUT, stderr=""),
    ]

    refresh()

    assert mock_run.call_args_list[0].args[0] == ["/managed/opencli", "daemon", "status"]
    assert mock_run.call_args_list[1].args[0] == ["/managed/opencli", "profile", "list"]


@patch("backend.infrastructure.browser.opencli_health.subprocess.run")
def test_refresh_handles_subprocess_timeout(mock_run):
    mock_run.side_effect = subprocess.TimeoutExpired(cmd="opencli", timeout=30)
    result = refresh()
    assert result["daemon_running"] is False
    assert result["extension_connected"] is False
    assert "hint" in result

# ---------------------------------------------------------------------------
# Real opencli 1.8.0 `profile list` output format
#
# Earlier opencli versions emitted: "  default — connected v1.0.2"
# 1.8.0 emits:                    "  demo0001 infoos-fetch default — connected v1.0.20"
# The first token is the contextId, the second is the alias, the third
# is the "default" tag (only when this profile is the defaultContextId),
# then the em-dash, then the status, optionally with a version.
# ---------------------------------------------------------------------------

OPENCLI_180_PROFILE_LIST = """Connected Browser Bridge profiles

  demo0001 infoos-fetch default — connected v1.0.20

  Update available: v1.8.0 → v1.8.4
  Run: npm install -g @jackwener/opencli
"""


def test_parse_profile_list_opencli_180_real_format():
    """Real opencli 1.8.0 emits contextId + alias + 'default' + status + version."""
    result = parse_profile_list_output(OPENCLI_180_PROFILE_LIST)
    assert len(result) == 1
    assert result[0]["alias"] == "demo0001"
    assert result[0]["status"] == "connected"
    assert result[0]["version"] == "1.0.20"


def test_parse_profile_list_opencli_180_no_default_tag():
    """Non-default profile:  contextId + alias — status (no "default" tag)."""
    sample = "  bbbbbbbb my-alias — connected v1.0.20\n"
    result = parse_profile_list_output(sample)
    assert len(result) == 1
    assert result[0]["alias"] == "bbbbbbbb"
    assert result[0]["status"] == "connected"


def test_parse_profile_list_old_format_still_parses():
    """The old format (alias — status version) must still parse — backward compat."""
    sample = "  default — connected v1.0.2\n"
    result = parse_profile_list_output(sample)
    assert len(result) == 1
    assert result[0]["alias"] == "default"
    assert result[0]["status"] == "connected"
    assert result[0]["version"] == "1.0.2"


def test_parse_profile_list_multiple_profiles():
    """Multiple connected profiles each yield one entry."""
    sample = (
        "  demo0001 infoos-fetch default — connected v1.0.20\n"
        "  bbbbbbbb my-alias — disconnected\n"
    )
    result = parse_profile_list_output(sample)
    assert len(result) == 2
    assert result[0]["alias"] == "demo0001"
    assert result[0]["status"] == "connected"
    assert result[1]["alias"] == "bbbbbbbb"
    assert result[1]["status"] == "disconnected"
