from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest

from backend.application.enrichment.asr.providers.faster_whisper_runtime_client import (
    FasterWhisperRuntimeClient,
)


_FAKE_FASTER_WHISPER = """
import os
import time


class _Segment:
    start = 0.0
    end = 1.0

    def __init__(self, text):
        self.text = text


class _Info:
    language = "zh"
    language_probability = 0.99


class WhisperModel:
    def __init__(self, _model, **_kwargs):
        counter = os.environ["FAKE_MODEL_COUNTER"]
        try:
            current = int(open(counter, encoding="utf-8").read())
        except (FileNotFoundError, ValueError):
            current = 0
        with open(counter, "w", encoding="utf-8") as stream:
            stream.write(str(current + 1))

    def transcribe(self, audio_path, **_options):
        if audio_path == "slow":
            time.sleep(1)
        return [_Segment(audio_path)], _Info()
"""


def _runtime_env(tmp_path: Path) -> tuple[dict[str, str], Path]:
    (tmp_path / "faster_whisper.py").write_text(
        _FAKE_FASTER_WHISPER,
        encoding="utf-8",
    )
    counter = tmp_path / "model-loads.txt"
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(tmp_path), str(Path.cwd()), env.get("PYTHONPATH", "")]
    )
    env["FAKE_MODEL_COUNTER"] = str(counter)
    return env, counter


def _payload(audio_path: str) -> dict[str, object]:
    return {
        "audio_path": audio_path,
        "model": "large-v3-turbo",
        "device": "cuda",
        "compute_type": "int8_float16",
        "download_root": "/models",
        "local_files_only": True,
    }


def test_runtime_client_reuses_the_same_process_and_model(tmp_path: Path) -> None:
    env, counter = _runtime_env(tmp_path)
    client = FasterWhisperRuntimeClient()
    try:
        first = client.request(_payload("first.wav"), timeout_seconds=2, env=env)
        second = client.request(_payload("second.wav"), timeout_seconds=2, env=env)
    finally:
        client.stop()

    assert first["text"] == "first.wav"
    assert second["text"] == "second.wav"
    assert counter.read_text(encoding="utf-8") == "1"


def test_runtime_client_restarts_after_timeout(tmp_path: Path) -> None:
    env, counter = _runtime_env(tmp_path)
    client = FasterWhisperRuntimeClient()
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            client.request(_payload("slow"), timeout_seconds=0.5, env=env)
        recovered = client.request(_payload("fast"), timeout_seconds=2, env=env)
    finally:
        client.stop()

    assert recovered["text"] == "fast"
    assert counter.read_text(encoding="utf-8") == "2"
