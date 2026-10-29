"""Short-lived faster-whisper CUDA runtime entrypoint."""
from __future__ import annotations

import json
import sys
from typing import Any


def create_model(payload: dict[str, Any]) -> Any:
    from faster_whisper import WhisperModel

    model_options = {
        "device": str(payload.get("device") or "cuda"),
        "compute_type": str(payload.get("compute_type") or "int8_float16"),
        "local_files_only": True,
    }
    if payload.get("download_root"):
        model_options["download_root"] = str(payload["download_root"])
    return WhisperModel(
        str(payload.get("model") or "large-v3-turbo"),
        **model_options,
    )


def transcribe_with_model(payload: dict[str, Any], model: Any) -> dict[str, Any]:
    options: dict[str, Any] = {"vad_filter": True}
    if payload.get("language"):
        options["language"] = str(payload["language"])
    generated, info = model.transcribe(str(payload["audio_path"]), **options)
    segments: list[dict[str, Any]] = []
    text_parts: list[str] = []
    for segment in generated:
        text = str(segment.text or "").strip()
        if not text:
            continue
        text_parts.append(text)
        segments.append(
            {
                "start": float(segment.start),
                "end": float(segment.end),
                "text": text,
            }
        )
    return {
        "status": "success",
        "text": "\n".join(text_parts),
        "segments": segments,
        "language": str(getattr(info, "language", "") or ""),
        "language_confidence": getattr(info, "language_probability", None),
        "duration_after_vad": _float_or_none(getattr(info, "duration_after_vad", None)),
    }


def transcribe(payload: dict[str, Any]) -> dict[str, Any]:
    return transcribe_with_model(payload, create_model(payload))


def _float_or_none(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def main() -> int:
    try:
        result = transcribe(json.loads(sys.stdin.read()))
    except Exception as exc:  # noqa: BLE001 - Faster-whisper and decoder failures must become the subprocess JSON failure response
        result = {
            "status": "failed",
            "error": "failed_asr_runtime_error",
            "quality_flags": [f"{type(exc).__name__}: {exc}"],
        }
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
