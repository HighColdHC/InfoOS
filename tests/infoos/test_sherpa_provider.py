from __future__ import annotations

from pathlib import Path
import json
import signal
import subprocess
import sys
import tomllib
import types

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

from backend.application.enrichment.asr.models import AsrRequest
from backend.application.enrichment.asr.providers.sherpa import SherpaOnnxProvider
from backend.application.enrichment.asr.providers.sherpa_subprocess import (
    transcribe_sensevoice,
)
from backend.application.enrichment.asr.router import SENSEVOICE_SMALL_INT8


SHERPA_PACKAGES = {"sherpa-onnx", "sherpa-onnx-bin", "sherpa-onnx-core"}


def _sherpa_abi_pins(requirements):
    pins = {}
    for line in requirements:
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        requirement = Requirement(line)
        name = canonicalize_name(requirement.name)
        if name not in SHERPA_PACKAGES:
            continue
        assert requirement.marker is None, f"{name} must be pinned on every platform"
        specifiers = list(requirement.specifier)
        assert len(specifiers) == 1, f"{name} needs one exact ABI pin"
        pin = specifiers[0]
        assert pin.operator == "==" and "*" not in pin.version, f"{name} is not exactly pinned"
        version = Version(pin.version)
        assert name not in pins or pins[name] == version, f"conflicting ABI pins for {name}"
        pins[name] = version
    assert set(pins) == SHERPA_PACKAGES
    assert len(set(pins.values())) == 1, f"sherpa split packages have different ABIs: {pins}"
    return pins


def test_media_extra_locks_abi_matched_sherpa_split_packages():
    project_root = Path(__file__).parents[2]
    with (project_root / "pyproject.toml").open("rb") as file:
        project = tomllib.load(file)
    constraints = (project_root / "deploy/docker/python-constraints-linux-amd64.txt").read_text(encoding="utf-8")

    media_pins = _sherpa_abi_pins(project["project"]["optional-dependencies"]["media"])
    assert _sherpa_abi_pins(constraints.splitlines()) == media_pins


def test_sherpa_provider_available_when_python_runtime_is_installed(monkeypatch, tmp_path: Path):
    model_dir = tmp_path / "data" / "asr_models" / "sensevoice-small-int8"
    model_dir.mkdir(parents=True)
    (model_dir / "model.int8.onnx").write_text("fake")
    (model_dir / "tokens.txt").write_text("fake")
    monkeypatch.setattr("backend.application.enrichment.asr.providers.sherpa.shutil.which", lambda _: None)
    monkeypatch.setitem(sys.modules, "sherpa_onnx", types.ModuleType("sherpa_onnx"))
    provider = SherpaOnnxProvider(base_dir=tmp_path)

    availability = provider.is_available()
    supports = provider.supports(
        AsrRequest(
            audio_path="/tmp/audio.wav",
            card_id="card_1",
            asset_id="asset_1",
            detected_language="zh",
            route_model="sensevoice-small-int8",
            model_profile=SENSEVOICE_SMALL_INT8,
        )
    )

    assert availability.available is True
    assert supports is True


def _request(tmp_path: Path) -> AsrRequest:
    return AsrRequest(
        audio_path=str(tmp_path / "audio.wav"),
        card_id="card_1",
        asset_id="asset_1",
        detected_language="zh",
        duration_seconds=20,
        route_model="sensevoice-small-int8",
        model_profile=SENSEVOICE_SMALL_INT8,
    )


def test_sherpa_provider_runs_in_isolated_subprocess_with_timeout(monkeypatch, tmp_path: Path):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({"status": "success", "text": "分段识别结果", "segments": []}),
            stderr="",
        )

    monkeypatch.setattr(
        "backend.application.enrichment.asr.providers.sherpa.subprocess.run",
        fake_run,
    )

    result = SherpaOnnxProvider(base_dir=tmp_path, timeout_seconds=12).transcribe(
        _request(tmp_path)
    )

    assert result.status == "success"
    assert result.text == "分段识别结果"
    assert captured["timeout"] == 12
    assert captured["command"] == [
        sys.executable,
        "-m",
        "backend.application.enrichment.asr.providers.sherpa_subprocess",
    ]


def test_sherpa_provider_timeout_returns_stable_terminal_blocked(monkeypatch, tmp_path: Path):
    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("sherpa", 1)

    monkeypatch.setattr(
        "backend.application.enrichment.asr.providers.sherpa.subprocess.run",
        timeout,
    )

    result = SherpaOnnxProvider(base_dir=tmp_path, timeout_seconds=1).transcribe(
        _request(tmp_path)
    )

    assert result.status == "blocked"
    assert result.error == "blocked_asr_timeout"


def test_sherpa_provider_sigkill_returns_stable_terminal_failed(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        "backend.application.enrichment.asr.providers.sherpa.subprocess.run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command,
            -signal.SIGKILL,
            stdout="",
            stderr="",
        ),
    )

    result = SherpaOnnxProvider(base_dir=tmp_path).transcribe(_request(tmp_path))

    assert result.status == "failed"
    assert result.error == "failed_asr_subprocess_sigkill"
    assert result.quality_flags == [f"returncode:{-signal.SIGKILL}"]


def test_sherpa_subprocess_decodes_each_audio_chunk_in_a_fresh_stream(
    monkeypatch,
) -> None:
    calls = {"create_stream": 0, "decode_stream": 0}

    class FakeSamples:
        def __init__(self, frames: int) -> None:
            self.frames = frames

        def __len__(self) -> int:
            return self.frames

        def __getitem__(self, key):
            assert key == (slice(None), 0)
            return [0.1] * self.frames

    class FakeStream:
        def __init__(self, index: int) -> None:
            self.result = types.SimpleNamespace(text=f"chunk {index}")

        def accept_waveform(self, sample_rate, samples) -> None:
            assert sample_rate == 4
            assert samples

    class FakeRecognizer:
        def create_stream(self):
            calls["create_stream"] += 1
            return FakeStream(calls["create_stream"])

        def decode_stream(self, _stream) -> None:
            calls["decode_stream"] += 1

    class FakeOfflineRecognizer:
        @staticmethod
        def from_sense_voice(**_kwargs):
            return FakeRecognizer()

    class FakeSoundFile:
        samplerate = 4

        def __init__(self, _path: str) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            pass

        def blocks(self, **_kwargs):
            yield FakeSamples(4)
            yield FakeSamples(4)

    monkeypatch.setitem(
        sys.modules,
        "sherpa_onnx",
        types.SimpleNamespace(OfflineRecognizer=FakeOfflineRecognizer),
    )
    monkeypatch.setitem(
        sys.modules,
        "soundfile",
        types.SimpleNamespace(SoundFile=FakeSoundFile),
    )

    result = transcribe_sensevoice(
        {"model_dir": "/models/sensevoice", "audio_path": "/audio.wav", "num_threads": 2}
    )

    assert calls == {"create_stream": 2, "decode_stream": 2}
    assert result["text"] == "chunk 1\nchunk 2"
    assert result["segments"] == [
        {"start": 0.0, "end": 1.0, "text": "chunk 1"},
        {"start": 1.0, "end": 2.0, "text": "chunk 2"},
    ]
