"""OpenCLIConnector lifecycle defaults and XHS persistent non-retained tabs."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.infrastructure.connectors.opencli import OpenCLIConnector  # noqa: E402
from backend.infrastructure.opencli.commands import pin_adapter_lifecycle  # noqa: E402


def _make_connector(keep_alive: bool = True) -> OpenCLIConnector:
    return OpenCLIConnector(
        connector_id="x_test",
        command=["twitter", "tweets", "test", "--limit", "5"],
        source_platform="x",
        source_type="social_post",
        keep_alive=keep_alive,
    )


def test_keep_alive_true_uses_background_window():
    conn = _make_connector(keep_alive=True)
    cmd = conn._command_with_json_format()
    assert "--window" in cmd
    idx = cmd.index("--window")
    assert cmd[idx + 1] == "background"


def test_keep_alive_false_uses_background_window():
    conn = _make_connector(keep_alive=False)
    cmd = conn._command_with_json_format()
    assert "--window" in cmd
    idx = cmd.index("--window")
    assert cmd[idx + 1] == "background"


def test_reddit_keep_alive_false_uses_ephemeral_lease():
    conn = OpenCLIConnector(
        connector_id="reddit_test",
        command=["reddit", "subreddit", "LocalLLaMA", "--limit", "5"],
        source_platform="reddit",
        source_type="social_post",
        keep_alive=False,
    )

    cmd = conn._command_with_json_format()

    assert cmd[cmd.index("--site-session") + 1] == "ephemeral"
    assert cmd[cmd.index("--keep-tab") + 1] == "false"
    assert cmd[:2] == ["--profile", "infoos-fetch"]


def test_reddit_keep_alive_true_still_uses_ephemeral_lease():
    conn = OpenCLIConnector(
        connector_id="reddit_test",
        command=["reddit", "subreddit", "LocalLLaMA", "--limit", "5"],
        source_platform="reddit",
        source_type="social_post",
        keep_alive=True,
    )

    cmd = conn._command_with_json_format()

    assert cmd[cmd.index("--site-session") + 1] == "ephemeral"
    assert cmd[cmd.index("--keep-tab") + 1] == "false"


def test_pin_adapter_lifecycle_defaults_to_ephemeral_tab_policy():
    cmd = pin_adapter_lifecycle(["twitter", "tweets", "test"])

    assert cmd[cmd.index("--site-session") + 1] == "ephemeral"
    assert cmd[cmd.index("--keep-tab") + 1] == "false"


def test_pin_adapter_lifecycle_can_select_persistent_nonretained_tab_policy():
    cmd = pin_adapter_lifecycle(
        ["xiaohongshu", "search", "AI"],
        site_session="persistent",
        keep_tab=False,
    )

    assert cmd[cmd.index("--site-session") + 1] == "persistent"
    assert cmd[cmd.index("--keep-tab") + 1] == "false"


def test_zsxq_keep_alive_false_uses_ephemeral_lease():
    conn = OpenCLIConnector(
        connector_id="zsxq_test",
        command=["zsxq", "topics", "--limit", "5"],
        source_platform="zsxq",
        source_type="article",
        keep_alive=False,
    )

    cmd = conn._command_with_json_format()

    assert cmd[cmd.index("--site-session") + 1] == "ephemeral"
    assert cmd[cmd.index("--keep-tab") + 1] == "false"


@pytest.mark.parametrize("platform, command", [
    ("x", ["twitter", "tweets", "elonmusk", "--limit", "5"]),
    ("youtube", ["youtube", "search", "infoos", "--limit", "5"]),
    ("douyin", ["douyin", "search", "AI", "--limit", "5"]),
])
def test_browser_backed_keep_alive_false_uses_ephemeral_lease(platform, command):
    conn = OpenCLIConnector(
        connector_id=f"{platform}_test",
        command=command,
        source_platform=platform,
        source_type="social_post",
        keep_alive=False,
    )

    cmd = conn._command_with_json_format()

    assert cmd[cmd.index("--site-session") + 1] == "ephemeral"
    assert cmd[cmd.index("--keep-tab") + 1] == "false"


def test_xiaohongshu_connector_uses_persistent_nonretained_tab_policy():
    conn = OpenCLIConnector(
        connector_id="xiaohongshu_test",
        command=["xiaohongshu", "search", "AI", "--limit", "5"],
        source_platform="xiaohongshu",
        source_type="social_post",
    )

    cmd = conn._command_with_json_format()

    assert cmd[cmd.index("--site-session") + 1] == "persistent"
    assert cmd[cmd.index("--keep-tab") + 1] == "false"


def test_keep_alive_default_is_true_for_backwards_compatibility():
    conn = OpenCLIConnector(
        connector_id="x_test",
        command=["twitter", "tweets", "test", "--limit", "5"],
        source_platform="x",
        source_type="social_post",
    )
    assert conn.keep_alive is True
    cmd = conn._command_with_json_format()
    assert "background" in cmd


def test_user_pinned_window_overrides_keep_alive():
    conn = OpenCLIConnector(
        connector_id="x_test",
        command=["twitter", "tweets", "test", "--limit", "5", "--window", "foreground"],
        source_platform="x",
        source_type="social_post",
        keep_alive=False,
    )
    cmd = conn._command_with_json_format()
    # No duplicate --window flag; the user-pinned one is preserved
    assert cmd.count("--window") == 1
    idx = cmd.index("--window")
    assert cmd[idx + 1] == "foreground"


def test_user_profile_is_replaced_with_dedicated_infoos_profile():
    conn = OpenCLIConnector(
        connector_id="x_test",
        command=["--profile", "personal", "twitter", "tweets", "test"],
        source_platform="x",
        source_type="social_post",
    )
    assert conn._command_with_json_format()[:2] == ["--profile", "infoos-fetch"]
