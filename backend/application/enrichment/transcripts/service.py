from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.application.enrichment.asr.models import AsrRequest
from backend.application.enrichment.asr.providers.faster_whisper import FasterWhisperCudaProvider
from backend.application.enrichment.asr.providers.sherpa import SherpaOnnxProvider
from backend.application.enrichment.asr.router import AsrRouter
from backend.application.enrichment.transcripts.native import write_native_transcript_block_if_available
from backend.infrastructure.runtime.environment import faster_whisper_cuda_enabled


def extract_video_transcript(
    *,
    base_dir: str | Path,
    asset: dict[str, Any],
    audio_path: str | None,
    duration_seconds: float | None = None,
    quality_mode: str | None = None,
) -> dict[str, Any]:
    native = write_native_transcript_block_if_available(base_dir, asset)
    if native is not None:
        return native
    if not audio_path:
        return {
            "status": "blocked",
            "text": "",
            "segments": [],
            "error": "blocked_audio_missing",
            "provider": "",
            "model": "",
            "fallback_chain": [],
            "quality_flags": [],
            "native_transcript_used": False,
            "route": "",
        }
    request = AsrRequest(
        audio_path=audio_path,
        card_id=asset["card_id"],
        asset_id=asset["asset_id"],
        language_hint=_language_hint(asset),
        detected_language=_detected_language(asset),
        language_confidence=_language_confidence(asset),
        duration_seconds=duration_seconds,
        quality_mode=quality_mode or _quality_mode(asset),
        max_cpu_threads=_max_cpu_threads(asset),
    )
    providers = []
    if faster_whisper_cuda_enabled():
        providers.append(FasterWhisperCudaProvider())
    providers.append(SherpaOnnxProvider(base_dir=base_dir))
    result = AsrRouter(providers).transcribe(request)
    payload = result.to_dict()
    payload["native_transcript_used"] = False
    return payload


def _raw_payload(asset: dict[str, Any]) -> dict[str, Any]:
    raw_payload = asset.get("raw_payload")
    return raw_payload if isinstance(raw_payload, dict) else {}


def _language_hint(asset: dict[str, Any]) -> str | None:
    raw = _raw_payload(asset)
    value = raw.get("language_hint") or raw.get("language")
    return str(value) if value else None


def _detected_language(asset: dict[str, Any]) -> str | None:
    raw = _raw_payload(asset)
    value = raw.get("detected_language")
    return str(value) if value else None


def _language_confidence(asset: dict[str, Any]) -> float | None:
    raw = _raw_payload(asset)
    try:
        return float(raw["language_confidence"])
    except (KeyError, TypeError, ValueError):
        return None


def _quality_mode(asset: dict[str, Any]) -> str:
    raw = _raw_payload(asset)
    return str(raw.get("asr_quality_mode") or "low_resource")


def _max_cpu_threads(asset: dict[str, Any]) -> int | None:
    raw = _raw_payload(asset)
    try:
        return int(raw["max_cpu_threads"])
    except (KeyError, TypeError, ValueError):
        return None
