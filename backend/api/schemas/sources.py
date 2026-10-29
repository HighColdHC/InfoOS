"""HTTP payload contracts for configurable source maintenance."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from backend.domain.sources.registry import source_type_capability


class SourceAiProcessingPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    provider_id: str = ""
    model: str = ""
    prompt: str = ""
    target_language: str = "zh-CN"


class SourceRawMetadataPayload(BaseModel):
    """User-facing media enrichment options retained in source metadata."""

    model_config = ConfigDict(extra="allow")

    auto_enrich_media: bool = True
    feed_provider: Literal["generic"] = "generic"
    feed_content_mode: Literal["feed_preferred"] = "feed_preferred"
    ai_processing: SourceAiProcessingPayload | None = None


class SourcePayload(BaseModel):
    """Create/update payload exposed by the collector source-management UI."""

    model_config = ConfigDict(extra="ignore")

    platform: str
    source_type: str
    identifier: str
    display_name: str
    cadence_hours: float = Field(gt=0)
    max_items_per_run: int = Field(default=20, ge=1)
    enabled: bool = True
    notes: str | None = None
    raw_metadata: SourceRawMetadataPayload = Field(default_factory=SourceRawMetadataPayload)

    # Transitional input compatibility for callers that already assigned an ID.
    # New clients omit it and receive the generated stable ID from the response.
    source_id: str | None = None


class SourceEnabledPayload(BaseModel):
    enabled: bool


def source_payload_dict(payload: SourcePayload) -> dict[str, Any]:
    """Map the HTTP shape to the application lifecycle input contract."""

    raw_metadata = payload.raw_metadata.model_dump(exclude_none=True)
    max_items = payload.max_items_per_run
    if (payload.platform == "douyin" and payload.source_type == "home_feed"
            and "max_items_per_run" not in payload.model_fields_set):
        max_items = source_type_capability("douyin", "home_feed").default_max_items_per_run
    raw_metadata["max_items_per_run"] = max_items
    return {
        "source_id": payload.source_id,
        "platform": payload.platform,
        "source_type": payload.source_type,
        "identifier": payload.identifier,
        "display_name": payload.display_name,
        "cadence_hours": payload.cadence_hours,
        "notes": payload.notes,
        "enabled": payload.enabled,
        "raw_metadata": raw_metadata,
    }
