from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from backend.application.enrichment.asr.language import EUROPEAN_LANGUAGE_CODES, normalize_language
from backend.application.enrichment.asr.models import AsrModelProfile, AsrProvider, AsrRequest, AsrResult
from backend.infrastructure.runtime.environment import faster_whisper_cuda_enabled

FASTER_WHISPER_LARGE_V3_TURBO = AsrModelProfile(
    model_id="large-v3-turbo",
    provider_id="faster-whisper-cuda",
    languages=("unknown", "en", "zh", "yue", "ja", "ko", *tuple(sorted(EUROPEAN_LANGUAGE_CODES))),
    resource_profile="local-gpu",
    quality_profile="high_quality",
)

SENSEVOICE_SMALL_INT8 = AsrModelProfile(
    model_id="sensevoice-small-int8",
    provider_id="sherpa-onnx",
    languages=("zh", "yue", "ja", "ko", "unknown"),
    resource_profile="local-low-resource",
    quality_profile="multilingual",
    directory_names=("sensevoice-small-int8", "sherpa-sensevoice-small-int8"),
)
MOONSHINE_TINY_INT8 = AsrModelProfile(
    model_id="moonshine-tiny-int8",
    provider_id="sherpa-onnx",
    languages=("en",),
    resource_profile="local-low-resource",
    quality_profile="routine",
    directory_names=("moonshine-tiny-int8", "sherpa-moonshine-tiny-int8"),
)
PARAKEET_INT8 = AsrModelProfile(
    model_id="parakeet-int8",
    provider_id="sherpa-onnx",
    languages=("en", *tuple(sorted(EUROPEAN_LANGUAGE_CODES))),
    resource_profile="local-higher-quality",
    quality_profile="high_quality",
    directory_names=("parakeet-int8", "sherpa-parakeet-int8"),
)


class AsrRouter:
    def __init__(self, providers: Iterable[AsrProvider]) -> None:
        self.providers = {provider.provider_id: provider for provider in providers}

    def transcribe(self, request: AsrRequest) -> AsrResult:
        candidates = _candidate_profiles(request)
        fallback_chain: list[str] = []
        last_blocked: tuple[str, str, str] | None = None
        for profile in candidates:
            fallback_chain.append(profile.model_id)
            provider = self.providers.get(profile.provider_id)
            if provider is None:
                last_blocked = (profile.provider_id, profile.model_id, _missing_provider_reason(profile.provider_id))
                continue
            availability = provider.is_available()
            if not availability.available:
                return AsrResult(
                    status="blocked",
                    error=availability.reason or _missing_provider_reason(profile.provider_id),
                    provider=provider.provider_id,
                    model=profile.model_id,
                    fallback_chain=fallback_chain,
                    route=profile.model_id,
                    quality_flags=[availability.message] if availability.message else [],
                )
            routed_request = replace(
                request,
                route_model=profile.model_id,
                route_provider=profile.provider_id,
                model_profile=profile,
            )
            if not provider.supports(routed_request):
                last_blocked = (provider.provider_id, profile.model_id, "blocked_asr_model_missing")
                continue
            result = provider.transcribe(routed_request)
            result.provider = result.provider or provider.provider_id
            result.model = result.model or profile.model_id
            result.route = result.route or profile.model_id
            result.fallback_chain = list(fallback_chain)
            if (
                result.status == "success"
                and provider.provider_id == "faster-whisper-cuda"
                and result.duration_after_vad is not None
                and result.duration_after_vad == 0
                and not result.text.strip()
                and not result.segments
            ):
                # An explicit Faster-whisper VAD result is the only signal
                # that turns an empty ASR result into a terminal
                # not-applicable outcome.  Other providers' empty text stays
                # a normal failed_empty_transcript result.
                result.status = "skipped"
                result.text = ""
                result.segments = []
                result.error = "no_speech_detected"
                return result
            if result.status == "success" and not result.text.strip():
                result.status = "failed"
                result.error = "failed_empty_transcript"
            if result.status == "success":
                result.text = result.text.strip()
            return result
        provider_id, model_id, reason = last_blocked or ("", "", "blocked_language_unsupported")
        return AsrResult(
            status="blocked",
            error=reason,
            provider=provider_id,
            model=model_id,
            fallback_chain=fallback_chain,
            route=model_id,
        )


def _candidate_profiles(request: AsrRequest) -> list[AsrModelProfile]:
    language = normalize_language(request.detected_language or request.language_hint)
    low_confidence = request.language_confidence is not None and request.language_confidence < 0.6
    quality_mode = request.quality_mode.lower()
    candidates: list[AsrModelProfile]
    if language in {"zh", "yue"}:
        candidates = [SENSEVOICE_SMALL_INT8]
    elif language == "en":
        if quality_mode in {"high_quality", "quality", "accurate"}:
            candidates = [PARAKEET_INT8]
        else:
            candidates = [MOONSHINE_TINY_INT8]
    elif language in {"ja", "ko"}:
        candidates = [SENSEVOICE_SMALL_INT8]
    elif language in EUROPEAN_LANGUAGE_CODES:
        candidates = [PARAKEET_INT8]
    elif low_confidence or language in {"unknown", "mixed", "und"}:
        candidates = [SENSEVOICE_SMALL_INT8]
    else:
        candidates = []
    if faster_whisper_cuda_enabled():
        return [FASTER_WHISPER_LARGE_V3_TURBO, *candidates]
    return candidates


def _missing_provider_reason(provider_id: str) -> str:
    if provider_id == "sherpa-onnx":
        return "blocked_sherpa_onnx_missing"
    if provider_id == "faster-whisper-cuda":
        return "blocked_faster_whisper_cuda_missing"
    return "blocked_asr_provider_missing"
