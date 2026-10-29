"""Built-in capability matrix（spec §11.1 总览表硬编码）。

每个 (platform, source_type) 的初始 status 与属性在此声明。
校准完成后由 record_calibration() 落库，再由 read_models 层组合展示。

修改本表前必须同步更新 spec §11.1。
"""
from __future__ import annotations

from dataclasses import replace

from backend.domain.calibration.matrix import CapabilityEntry
from backend.domain.sources.registry import configurable_platforms


def _entry(platform, source_type, status, requires_login, login_verifiable,
           cadence, known_gap=None):
    return CapabilityEntry(
        platform=platform,
        source_type=source_type,
        status=status,
        requires_login=requires_login,
        login_verifiable=login_verifiable,
        default_cadence=cadence,
        calibrated_at=None,
        calibration_result=None,
        known_gap=known_gap,
    )


BUILTIN_CAPABILITIES: list[CapabilityEntry] = [
    _entry(
        platform.platform,
        source.source_type,
        source.initial_status,
        source.requires_login or platform.requires_login,
        source.login_verifiable or platform.login_verifiable,
        source.default_cadence,
    )
    for platform in configurable_platforms()
    for source in platform.source_types
]


def builtin_capability_for(platform: str, source_type: str) -> CapabilityEntry | None:
    """Return a built-in entry using the canonical platform aliases."""
    from backend.domain.sources.registry import canonical_platform as normalize_platform

    canonical_platform = normalize_platform(platform)
    normalized_source_type = str(source_type).strip().lower()
    for entry in BUILTIN_CAPABILITIES:
        if entry.platform == canonical_platform and entry.source_type == normalized_source_type:
            return replace(entry, platform=canonical_platform)
    return None
