from __future__ import annotations

from dataclasses import dataclass

from backend.domain.sources.registry import PLATFORM_REGISTRY, browser_platforms as registry_browser_platforms


@dataclass(frozen=True)
class PlatformCapability:
    platform: str
    requires_login: bool
    can_verify_login: bool
    can_fetch: bool
    label: str

    @property
    def supported(self) -> bool:
        return self.can_fetch and (not self.requires_login or self.can_verify_login)


PLATFORM_CAPABILITIES: dict[str, PlatformCapability] = {
    key: PlatformCapability(
        platform=value.platform,
        requires_login=value.requires_login == "required",
        can_verify_login=value.can_verify_login,
        can_fetch=value.can_fetch or not value.source_types,
        label=value.label,
    )
    for key, value in PLATFORM_REGISTRY.items()
    if value.requires_browser_session
}


def browser_platforms() -> tuple[str, ...]:
    return registry_browser_platforms()


def platform_capability(platform: str) -> PlatformCapability:
    return PLATFORM_CAPABILITIES[platform]
