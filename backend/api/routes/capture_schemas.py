"""Strict wire schemas for capture.v1."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CaptureClient(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=64)


class RenderedContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["readability_html"]
    html: str | None = None
    text: str | None = None
    byline: str | None = Field(default=None, max_length=512)
    excerpt: str | None = Field(default=None, max_length=2048)
    site_name: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def require_body(self) -> "RenderedContent":
        if not (self.html or "").strip() and not (self.text or "").strip():
            raise ValueError("rendered content is empty")
        return self


class CaptureRequestV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_name: Literal["infoos.capture-request.v1"] = Field(alias="schema")
    client_capture_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    url: str
    canonical_url: str | None = None
    title: str = ""
    selected_text: str | None = None
    page_language: str | None = Field(default=None, max_length=64)
    captured_at: datetime
    capture_mode: Literal["url_only", "readable_content"]
    rendered_content: RenderedContent | None = None
    client: CaptureClient

    @model_validator(mode="after")
    def match_capture_mode(self) -> "CaptureRequestV1":
        if self.capture_mode == "url_only" and self.rendered_content is not None:
            raise ValueError("url_only must not include rendered_content")
        if self.capture_mode == "readable_content" and self.rendered_content is None:
            raise ValueError("readable_content requires rendered_content")
        if self.captured_at.tzinfo is None:
            raise ValueError("captured_at must include a timezone")
        return self
