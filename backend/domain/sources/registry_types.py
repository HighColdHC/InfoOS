"""JSON-ready capability catalog contracts."""
from typing import TypedDict


class IdentifierOption(TypedDict):
    value: str
    label: str


class SourceTypeItem(TypedDict):
    source_type: str
    label: str
    initial_status: str
    default_cadence: str
    default_cadence_hours: float
    default_max_items: int | None
    identifier_mode: str
    identifier_hint: str | None
    identifier_options: list[IdentifierOption]
    requires_login: str
    login_verifiable: str
    max_items_limit: int
    research_capable: bool


class ResearchCapabilityItem(TypedDict):
    status: str
    mode: str
    max_items_limit: int
    supports_date_range: bool
    requires_source_selection: bool
    limitation: str | None
    sort_options: list[str]
    default_sort: str


class PlatformCapabilityItem(TypedDict):
    platform: str
    label: str
    source_types: list[SourceTypeItem]
    cadence_options: list[float]
    requires_login: str
    login_verifiable: str
    requires_browser_session: bool
    configurable: bool
    default_max_items: int | None
    max_items_limit: int


class CatalogItem(PlatformCapabilityItem):
    research: ResearchCapabilityItem | None
