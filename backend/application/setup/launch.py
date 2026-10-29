"""Application-facing setup browser launch action."""
from __future__ import annotations

from pathlib import Path

from backend.infrastructure.browser.setup_launcher import launch_infoos_chrome as _launch


def launch_infoos_chrome(
    *,
    launcher: Path | None = None,
    start_url: str = "https://x.com/home",
    open_webstore: bool = False,
) -> dict:
    """Launch the visible dedicated browser through the infrastructure adapter."""
    return _launch(launcher=launcher, start_url=start_url, open_webstore=open_webstore)
