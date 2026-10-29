"""Provider-neutral public-document acquisition contracts and evidence."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class PublicDocumentRequest:
    url: str
    headers: dict[str, str]
    timeout_seconds: float
    max_bytes: int


@dataclass(frozen=True)
class PublicDocument:
    provider: str
    final_url: str
    title: str | None
    content: str
    readable_html: str | None
    links: tuple[str, ...]
    images: tuple[str, ...]
    videos: tuple[str, ...]
    fetch_tier: str
    cache_hit: bool | None
    status_code: int | None
    elapsed_ms: int
    response_bytes: int | None
    content_hash: str

    def completeness_evidence(self) -> dict[str, object]:
        content_chars = len(self.content)
        content_to_response_ratio = (
            round(content_chars / self.response_bytes, 6)
            if self.response_bytes
            else None
        )
        abnormal_low_text_yield = bool(
            self.response_bytes is not None
            and self.response_bytes >= 5_000
            and content_chars < 500
        )
        return {
            "provider": self.provider,
            "final_url": self.final_url,
            "fetch_tier": self.fetch_tier,
            "cache_hit": self.cache_hit,
            "status_code": self.status_code,
            "elapsed_ms": self.elapsed_ms,
            "response_bytes": self.response_bytes,
            "content_chars": content_chars,
            "content_to_response_ratio": content_to_response_ratio,
            "abnormal_low_text_yield": abnormal_low_text_yield,
            "link_count": len(self.links),
            "image_count": len(self.images),
            "video_count": len(self.videos),
            "has_title": bool(self.title),
            "content_hash": self.content_hash,
        }


@dataclass(frozen=True)
class PublicDocumentFailure:
    provider: str
    failure_class: str
    elapsed_ms: int

    def evidence(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "failure_class": self.failure_class,
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass(frozen=True)
class PublicDocumentOutcome:
    document: PublicDocument | None = None
    failure: PublicDocumentFailure | None = None

    def __post_init__(self) -> None:
        if (self.document is None) == (self.failure is None):
            raise ValueError("public document outcome requires exactly one result")


class PublicDocumentProvider(Protocol):
    name: str

    def fetch(self, request: PublicDocumentRequest) -> PublicDocumentOutcome: ...


def compare_public_documents(
    primary: PublicDocument,
    shadow: PublicDocument,
) -> dict[str, object]:
    primary_chars = len(primary.content)
    shadow_chars = len(shadow.content)
    primary_links = len(primary.links)
    shadow_links = len(shadow.links)
    content_ratio = round(shadow_chars / primary_chars, 4) if primary_chars else None
    link_ratio = round(shadow_links / primary_links, 4) if primary_links else None
    suspicious_content_gap = bool(
        primary_chars >= 10_000
        and content_ratio is not None
        and content_ratio < 0.25
    )
    suspicious_link_gap = bool(
        primary_links >= 100
        and link_ratio is not None
        and link_ratio < 0.25
    )
    primary_extraction_gap = bool(
        primary_chars < 500
        and shadow_chars >= 1_000
        and shadow_chars >= max(1, primary_chars) * 10
    )
    return {
        "primary_provider": primary.provider,
        "shadow_provider": shadow.provider,
        "same_final_url": primary.final_url == shadow.final_url,
        "same_content_hash": primary.content_hash == shadow.content_hash,
        "shadow_to_primary_content_ratio": content_ratio,
        "shadow_to_primary_link_ratio": link_ratio,
        "shadow_content_gap_suspected": suspicious_content_gap,
        "shadow_link_recall_gap": suspicious_link_gap,
        "shadow_truncation_suspected": suspicious_content_gap,
        "primary_extraction_gap_suspected": primary_extraction_gap,
    }
