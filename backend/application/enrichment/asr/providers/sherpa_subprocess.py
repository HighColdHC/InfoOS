"""Isolated sherpa-onnx runtime entrypoint.

The task worker launches this file as a child process so native runtime crashes,
OOM kills, and stuck inference cannot terminate or indefinitely block the worker.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


_AUDIO_CHUNK_FRAMES = 480_000


def transcribe_sensevoice(payload: dict[str, Any]) -> dict[str, Any]:
    import sherpa_onnx
    import soundfile as sf

    model_dir = Path(str(payload["model_dir"]))
    recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=str(model_dir / "model.int8.onnx"),
        tokens=str(model_dir / "tokens.txt"),
        use_itn=True,
        num_threads=max(1, int(payload.get("num_threads") or 2)),
        debug=False,
        provider="cpu",
    )
    text_parts: list[str] = []
    segments: list[dict[str, Any]] = []
    frame_offset = 0
    with sf.SoundFile(str(payload["audio_path"])) as audio:
        sample_rate = int(audio.samplerate)
        for samples in audio.blocks(
            blocksize=_AUDIO_CHUNK_FRAMES,
            dtype="float32",
            always_2d=True,
        ):
            stream = recognizer.create_stream()
            stream.accept_waveform(sample_rate, samples[:, 0])
            recognizer.decode_stream(stream)
            result = stream.result
            text = str(getattr(result, "text", result)).strip()
            chunk_frames = len(samples)
            if text:
                text_parts.append(text)
                segments.append(
                    {
                        "start": frame_offset / sample_rate,
                        "end": (frame_offset + chunk_frames) / sample_rate,
                        "text": text,
                    }
                )
            frame_offset += chunk_frames
    return {
        "status": "success",
        "text": "\n".join(text_parts),
        "segments": segments,
    }


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
        result = transcribe_sensevoice(payload)
    except Exception as exc:  # noqa: BLE001 - Sherpa and audio library failures must become the subprocess JSON failure response
        result = {
            "status": "failed",
            "error": "failed_asr_runtime_error",
            "quality_flags": [f"{type(exc).__name__}: {exc}"],
        }
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
