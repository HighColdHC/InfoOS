"""Fail-closed ownership policy for deployed Linux browser mutations.

InfoOS still supports a native dedicated Chrome on macOS for development and
the isolated test environment. The deployed Linux state root is different: it
is shared by the historical systemd services and the Docker Appliance, so a
path match alone cannot prove that a browser process belongs to the container.
"""
from __future__ import annotations

import os
from pathlib import Path

from backend.infrastructure.runtime.environment import chrome_profile_dir, state_home


DEPLOYED_STATE_HOME = Path("/var/lib/infoos/state")
DEPLOYED_CHROME_PROFILE = DEPLOYED_STATE_HOME / "chrome-profile"
SUPERVISED_CONTAINER_RUNTIME = "supervised-container"
_DOCKER_MARKER = Path("/.dockerenv")


def browser_mutation_block_reason() -> str | None:
    """Return a stable reason when this process may not mutate browser state.

    Native macOS development/test profiles remain supported. Once the deployed
    Linux state contract is selected, however, mutations are valid only from
    the supervised Docker Appliance itself. This prevents the legacy systemd
    Web/worker from launching or controlling a host Chrome that happens to use
    the same logical profile path as the appliance.
    """
    if _normalized(state_home()) != DEPLOYED_STATE_HOME:
        return None

    runtime = os.environ.get("INFOOS_BROWSER_RUNTIME", "").strip()
    if runtime != SUPERVISED_CONTAINER_RUNTIME:
        return (
            "Container Browser Runtime is required for deployed InfoOS browser "
            "operations; the native/systemd runtime is read-only"
        )
    if not _DOCKER_MARKER.is_file():
        return (
            "Container Browser Runtime is required for deployed InfoOS browser "
            "operations; Docker container ownership was not proven"
        )
    if _normalized(chrome_profile_dir()) != DEPLOYED_CHROME_PROFILE:
        return (
            "Container Browser Runtime requires the dedicated "
            "/var/lib/infoos/state/chrome-profile"
        )
    return None


def _normalized(path: Path) -> Path:
    return Path(os.path.abspath(os.path.normpath(str(path.expanduser()))))
