from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

AsrStatus = Literal["success", "blocked", "failed", "skipped"]


@dataclass(frozen=True)
class ProviderAvailability:
    provider_id: str
    available: bool
    reason: str | None = None
    message: str | None = None

    @classmethod
    def available(cls, *, provider_id: str) -> "ProviderAvailability":
        return cls(provider_id=provider_id, available=True)

    @classmethod
    def blocked(cls, *, provider_id: str, reason: str, message: str | None = None) -> "ProviderAvailability":
        return cls(provider_id=provider_id, available=False, reason=reason, message=message)


@dataclass(frozen=True)
class AsrModelProfile:
    model_id: str
    provider_id: str
    languages: tuple[str, ...]
    resource_profile: str
    quality_profile: str
    directory_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class AsrRequest:
    audio_path: str
    card_id: str
    asset_id: str
    language_hint: str | None = None
    detected_language: str | None = None
    language_confidence: float | None = None
    duration_seconds: float | None = None
    quality_mode: str = "low_resource"
    max_cpu_threads: int | None = None
    route_model: str | None = None
    route_provider: str | None = None
    model_profile: AsrModelProfile | None = None


@dataclass
class AsrResult:
    text: str = ""
    segments: list[dict[str, Any]] = field(default_factory=list)
    language: str = ""
    language_confidence: float | None = None
    provider: str = ""
    model: str = ""
    duration_seconds: float | None = None
    # Faster-whisper reports the amount of audio left after its VAD pass.
    # ``None`` means the provider did not report the value; only an explicit
    # zero is allowed to classify a successful Faster-whisper result as
    # ``no_speech_detected``.
    duration_after_vad: float | None = None
    rtf: float | None = None
    fallback_chain: list[str] = field(default_factory=list)
    error: str | None = None
    quality_flags: list[str] = field(default_factory=list)
    status: AsrStatus = "success"
    route: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "text": self.text,
            "segments": self.segments,
            "language": self.language,
            "language_confidence": self.language_confidence,
            "provider": self.provider,
            "model": self.model,
            "duration_seconds": self.duration_seconds,
            "duration_after_vad": self.duration_after_vad,
            "rtf": self.rtf,
            "fallback_chain": self.fallback_chain,
            "error": self.error,
            "quality_flags": self.quality_flags,
            "route": self.route or self.model,
        }


class AsrProvider(Protocol):
    provider_id: str
    resource_profile: str

    def is_available(self) -> ProviderAvailability: ...

    def supports(self, request: AsrRequest) -> bool: ...

    def transcribe(self, request: AsrRequest) -> AsrResult: ...
