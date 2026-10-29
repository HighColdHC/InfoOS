"""Domain rules that govern a source entering the fetch queue."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from backend.domain.calibration.builtin_matrix import builtin_capability_for
from backend.domain.calibration.matrix import (
    LOGIN_NOT_APPLICABLE,
    LOGIN_VERIFIABLE,
    NO_LOGIN,
    REQUIRED_LOGIN,
    CapabilityEntry,
    can_enter_fetch_queue,
    compute_capability,
)
from backend.domain.calibration.record import Calibration
from backend.domain.sources.configuration import SUPPORTED_EXECUTION
from backend.domain.sources.registry import platform_definition

_PLATFORM_ALIASES = {"xhs": "xiaohongshu", "twitter": "x"}


def resolve_source_capability(
    source: Mapping[str, Any],
    *,
    calibration: Calibration | None = None,
) -> CapabilityEntry:
    """Return the canonical capability for a source from its SQLite status and matrix.

    ``connector_status`` is the persisted calibration outcome.  It can promote
    an executable source to ``supported`` or ``partial``; the static matrix
    still has final authority for explicitly unsupported source kinds.
    """
    platform = _normalize_platform(source.get("platform"))
    source_type = str(source.get("source_type") or "").strip().lower()
    if (
        str(source.get("source_scope") or "maintained") == "research"
        and source_type == "research"
    ):
        definition = platform_definition(platform)
        if definition is not None and definition.research is not None:
            return compute_capability(
                platform=platform,
                source_type=source_type,
                code_supported=True,
                calibration=calibration,
                requires_login=definition.requires_login,
                login_verifiable=definition.login_verifiable,
            )
    builtin = builtin_capability_for(platform, source_type)
    persisted_status = str(source.get("connector_status") or "").strip().lower()

    if persisted_status == "unsupported":
        return _computed_capability(
            platform,
            source_type,
            calibration=calibration,
            builtin=builtin,
            explicit_unsupported=True,
        )

    if builtin is not None and builtin.status == "unsupported":
        return _computed_capability(
            platform,
            source_type,
            calibration=calibration,
            builtin=builtin,
            explicit_unsupported=True,
        )

    if persisted_status in {"supported", "partial"}:
        computed = _computed_capability(
            platform,
            source_type,
            calibration=calibration,
            builtin=builtin,
            code_supported=(platform, source_type) in SUPPORTED_EXECUTION,
        )
        if computed.status not in {"planned", "unsupported"}:
            return replace(computed, status=persisted_status)

    if builtin is not None:
        if calibration is None:
            return builtin
        return _computed_capability(
            platform,
            source_type,
            calibration=calibration,
            builtin=builtin,
            explicit_unsupported=builtin.status == "unsupported",
        )

    return _computed_capability(
        platform,
        source_type,
        calibration=calibration,
        code_supported=(platform, source_type) in SUPPORTED_EXECUTION,
    )


def source_can_enter_fetch_queue(source: dict[str, Any]) -> bool:
    """Only calibrated or explicitly partial sources may be queued automatically."""
    if (
        str(source.get("source_scope") or "maintained") == "research"
        and str(source.get("source_type") or "") == "research"
        and str(source.get("connector_status") or "") == "supported"
    ):
        return True
    return can_enter_fetch_queue(resolve_source_capability(source))


def _computed_capability(
    platform: str,
    source_type: str,
    *,
    calibration: Calibration | None,
    builtin: CapabilityEntry | None = None,
    code_supported: bool | None = None,
    explicit_unsupported: bool = False,
) -> CapabilityEntry:
    if builtin is not None:
        code_supported = builtin.status in {"supported", "implemented_uncalibrated", "partial"}
        return compute_capability(
            platform=platform,
            source_type=source_type,
            code_supported=code_supported,
            calibration=calibration,
            default_cadence=builtin.default_cadence,
            requires_login=builtin.requires_login,
            login_verifiable=builtin.login_verifiable,
            known_gap=builtin.known_gap or ("built-in partial capability" if builtin.status == "partial" else None),
            explicit_unsupported=explicit_unsupported,
        )

    requires_login = NO_LOGIN if platform in {"rss", "github", "hackernews", "producthunt"} else REQUIRED_LOGIN
    login_verifiable = LOGIN_NOT_APPLICABLE if requires_login == NO_LOGIN else LOGIN_VERIFIABLE
    return compute_capability(
        platform=platform,
        source_type=source_type,
        code_supported=bool(code_supported),
        calibration=calibration,
        requires_login=requires_login,
        login_verifiable=login_verifiable,
        explicit_unsupported=explicit_unsupported,
    )


def _normalize_platform(value: Any) -> str:
    platform = str(value or "").strip().lower()
    return _PLATFORM_ALIASES.get(platform, platform)
