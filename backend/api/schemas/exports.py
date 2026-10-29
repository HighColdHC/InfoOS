"""HTTP payload schemas for the frozen export request contract."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ExportRequestPayload(BaseModel):
    kind: str
    format: str = "json"
    time_from: str | None = None
    time_to: str | None = None
    include_sources_runs: bool = False
    include_cards_content: bool = False
    include_asset_index: bool = True
    include_attachment_files: bool = False
    include_redacted_logs: bool = False
    include_media_files: bool = False
    log_levels: list[str] = Field(default_factory=list)
    include_environment_snapshot: bool = False
    source_run_ids: list[str] = Field(default_factory=list)
