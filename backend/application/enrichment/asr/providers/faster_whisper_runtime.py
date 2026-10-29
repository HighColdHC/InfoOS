"""Long-lived faster-whisper runtime with a line-delimited JSON protocol."""
from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from backend.application.enrichment.asr.providers.faster_whisper_subprocess import (
    create_model,
    transcribe_with_model,
)


def _model_key(payload: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(payload.get("model") or "large-v3-turbo"),
        str(payload.get("device") or "cuda"),
        str(payload.get("compute_type") or "int8_float16"),
        str(payload.get("download_root") or ""),
    )


def _failure(exc: Exception) -> dict[str, Any]:
    return {
        "status": "failed",
        "error": "failed_asr_runtime_error",
        "quality_flags": [type(exc).__name__],
    }


def serve(*, input_stream: TextIO, output_stream: TextIO) -> int:
    model: Any | None = None
    loaded_key: tuple[str, str, str, str] | None = None
    for line in input_stream:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise TypeError("request payload must be an object")
            key = _model_key(payload)
            if model is None or loaded_key != key:
                model = create_model(payload)
                loaded_key = key
            result = transcribe_with_model(payload, model)
        except Exception as exc:  # noqa: BLE001 - Faster-whisper library failures must produce one JSON response per request and keep the runtime alive
            result = _failure(exc)
        output_stream.write(json.dumps(result, ensure_ascii=False) + "\n")
        output_stream.flush()
    return 0


def main() -> int:
    return serve(input_stream=sys.stdin, output_stream=sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
