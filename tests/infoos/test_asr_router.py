from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from backend.application.enrichment.asr.models import AsrRequest, AsrResult, ProviderAvailability
from backend.application.enrichment.asr.providers.faster_whisper import FasterWhisperCudaProvider
from backend.application.enrichment.asr.providers.faster_whisper_subprocess import transcribe as transcribe_faster_whisper
from backend.application.enrichment.asr.router import AsrRouter


class FakeProvider:
    def __init__(
        self,
        provider_id: str,
        *,
        available: bool = True,
        supported_models: set[str] | None = None,
        text: str = "Transcript text.",
        segments: list[dict[str, object]] | None = None,
        duration_after_vad: float | None = None,
    ) -> None:
        self.provider_id = provider_id
        self.resource_profile = "test"
        self.available = available
        self.supported_models = supported_models or set()
        self.text = text
        self.segments = segments
        self.duration_after_vad = duration_after_vad
        self.requests: list[AsrRequest] = []

    def is_available(self) -> ProviderAvailability:
        if self.available:
            return ProviderAvailability.available(provider_id=self.provider_id)
        return ProviderAvailability.blocked(
            provider_id=self.provider_id,
            reason="blocked_sherpa_onnx_missing",
            message="sherpa-onnx runtime is missing",
        )

    def supports(self, request: AsrRequest) -> bool:
        return bool(request.route_model and request.route_model in self.supported_models)

    def transcribe(self, request: AsrRequest) -> AsrResult:
        self.requests.append(request)
        return AsrResult(
            text=self.text,
            segments=(
                self.segments
                if self.segments is not None
                else [{"start": 0.0, "end": 1.0, "text": self.text}]
            ),
            language=request.detected_language or request.language_hint or "en",
            language_confidence=0.9,
            provider=self.provider_id,
            model=request.route_model or "",
            duration_seconds=request.duration_seconds,
            duration_after_vad=self.duration_after_vad,
            rtf=0.2,
        )


def _request(**overrides: Any) -> AsrRequest:
    data = {
        "audio_path": "/tmp/audio.wav",
        "card_id": "card_1",
        "asset_id": "asset_1",
        "language_hint": None,
        "detected_language": None,
        "duration_seconds": 10.0,
        "quality_mode": "low_resource",
        "max_cpu_threads": 2,
    }
    data.update(overrides)
    return AsrRequest(**data)


def test_router_routes_chinese_to_sensevoice() -> None:
    sherpa = FakeProvider("sherpa-onnx", supported_models={"sensevoice-small-int8"})

    result = AsrRouter([sherpa]).transcribe(_request(detected_language="zh"))

    assert result.status == "success"
    assert result.provider == "sherpa-onnx"
    assert result.model == "sensevoice-small-int8"
    assert result.fallback_chain == ["sensevoice-small-int8"]


def test_router_prefers_explicitly_enabled_faster_whisper_cuda(monkeypatch) -> None:
    monkeypatch.setenv("INFOOS_FASTER_WHISPER_CUDA_ENABLED", "1")
    cuda = FakeProvider("faster-whisper-cuda", supported_models={"large-v3-turbo"})
    sherpa = FakeProvider("sherpa-onnx", supported_models={"sensevoice-small-int8"})

    result = AsrRouter([cuda, sherpa]).transcribe(_request(detected_language="zh"))

    assert result.status == "success"
    assert result.provider == "faster-whisper-cuda"
    assert result.model == "large-v3-turbo"
    assert sherpa.requests == []


def test_router_keeps_existing_default_when_cuda_is_not_enabled(monkeypatch) -> None:
    monkeypatch.delenv("INFOOS_FASTER_WHISPER_CUDA_ENABLED", raising=False)
    cuda = FakeProvider("faster-whisper-cuda", supported_models={"large-v3-turbo"})
    sherpa = FakeProvider("sherpa-onnx", supported_models={"sensevoice-small-int8"})

    result = AsrRouter([cuda, sherpa]).transcribe(_request(detected_language="zh"))

    assert result.provider == "sherpa-onnx"
    assert cuda.requests == []


def test_router_routes_english_low_resource_to_moonshine() -> None:
    sherpa = FakeProvider("sherpa-onnx", supported_models={"moonshine-tiny-int8"})

    result = AsrRouter([sherpa]).transcribe(_request(detected_language="en"))

    assert result.status == "success"
    assert result.model == "moonshine-tiny-int8"


def test_router_routes_english_high_quality_to_parakeet() -> None:
    sherpa = FakeProvider("sherpa-onnx", supported_models={"parakeet-int8"})

    result = AsrRouter([sherpa]).transcribe(
        _request(detected_language="en", quality_mode="high_quality")
    )

    assert result.status == "success"
    assert result.provider == "sherpa-onnx"
    assert result.model == "parakeet-int8"


def test_router_blocks_missing_multilingual_model_for_low_confidence_unknown_language() -> None:
    sherpa = FakeProvider("sherpa-onnx", supported_models=set())

    result = AsrRouter([sherpa]).transcribe(
        _request(detected_language="unknown", language_confidence=0.2)
    )

    assert result.status == "blocked"
    assert result.error == "blocked_asr_model_missing"
    assert result.provider == "sherpa-onnx"
    assert result.model == "sensevoice-small-int8"
    assert result.fallback_chain == ["sensevoice-small-int8"]


def test_router_reports_missing_sherpa_runtime_as_blocked() -> None:
    sherpa = FakeProvider("sherpa-onnx", available=False, supported_models={"sensevoice-small-int8"})

    result = AsrRouter([sherpa]).transcribe(_request(detected_language="zh"))

    assert result.status == "blocked"
    assert result.error == "blocked_sherpa_onnx_missing"
    assert result.provider == "sherpa-onnx"
    assert result.model == "sensevoice-small-int8"


def test_router_reports_missing_model_as_blocked() -> None:
    sherpa = FakeProvider("sherpa-onnx", supported_models=set())

    result = AsrRouter([sherpa]).transcribe(_request(detected_language="zh"))

    assert result.status == "blocked"
    assert result.error == "blocked_asr_model_missing"
    assert result.fallback_chain == ["sensevoice-small-int8"]


def test_router_reports_empty_transcript_as_failed() -> None:
    sherpa = FakeProvider("sherpa-onnx", supported_models={"moonshine-tiny-int8"}, text=" ")

    result = AsrRouter([sherpa]).transcribe(_request(detected_language="en"))

    assert result.status == "failed"
    assert result.error == "failed_empty_transcript"


def test_router_marks_only_explicit_faster_whisper_no_speech_as_not_applicable(monkeypatch) -> None:
    monkeypatch.setenv("INFOOS_FASTER_WHISPER_CUDA_ENABLED", "1")
    cuda = FakeProvider(
        "faster-whisper-cuda",
        supported_models={"large-v3-turbo"},
        text=" ",
        segments=[],
        duration_after_vad=0.0,
    )
    sherpa = FakeProvider(
        "sherpa-onnx",
        supported_models={"sensevoice-small-int8"},
        text=" ",
    )

    result = AsrRouter([cuda, sherpa]).transcribe(_request(detected_language="zh"))

    assert result.status == "skipped"
    assert result.error == "no_speech_detected"
    assert result.text == ""
    assert result.fallback_chain == ["large-v3-turbo"]
    assert sherpa.requests == []


def test_router_keeps_blank_faster_whisper_segments_as_failed_empty_transcript(monkeypatch) -> None:
    monkeypatch.setenv("INFOOS_FASTER_WHISPER_CUDA_ENABLED", "1")
    cuda = FakeProvider(
        "faster-whisper-cuda",
        supported_models={"large-v3-turbo"},
        text=" ",
        segments=[{"start": 0.0, "end": 1.0, "text": " "}],
        duration_after_vad=0.0,
    )

    result = AsrRouter([cuda]).transcribe(_request(detected_language="zh"))

    assert result.status == "failed"
    assert result.error == "failed_empty_transcript"


def test_router_does_not_treat_non_faster_whisper_empty_transcript_as_no_speech() -> None:
    sherpa = FakeProvider(
        "sherpa-onnx",
        supported_models={"sensevoice-small-int8"},
        text=" ",
        duration_after_vad=0.0,
    )

    result = AsrRouter([sherpa]).transcribe(_request(detected_language="zh"))

    assert result.status == "failed"
    assert result.error == "failed_empty_transcript"


def test_router_keeps_faster_whisper_empty_transcript_failed_without_explicit_vad_duration(
    monkeypatch,
) -> None:
    monkeypatch.setenv("INFOOS_FASTER_WHISPER_CUDA_ENABLED", "1")
    cuda = FakeProvider(
        "faster-whisper-cuda",
        supported_models={"large-v3-turbo"},
        text=" ",
    )

    result = AsrRouter([cuda]).transcribe(_request(detected_language="zh"))

    assert result.status == "failed"
    assert result.error == "failed_empty_transcript"


def test_router_preserves_non_empty_faster_whisper_transcript_when_vad_duration_is_zero(
    monkeypatch,
) -> None:
    monkeypatch.setenv("INFOOS_FASTER_WHISPER_CUDA_ENABLED", "1")
    cuda = FakeProvider(
        "faster-whisper-cuda",
        supported_models={"large-v3-turbo"},
        text="Real transcript.",
        duration_after_vad=0.0,
    )

    result = AsrRouter([cuda]).transcribe(_request(detected_language="zh"))

    assert result.status == "success"
    assert result.text == "Real transcript."
    assert result.error is None


def test_faster_whisper_cuda_uses_isolated_subprocess_and_shared_gpu_lock(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("INFOOS_FASTER_WHISPER_CUDA_ENABLED", "1")
    monkeypatch.setenv("INFOOS_FASTER_WHISPER_PERSISTENT_RUNTIME_ENABLED", "0")
    monkeypatch.setenv(
        "INFOOS_FASTER_WHISPER_DOWNLOAD_ROOT",
        "/var/lib/infoos/state/models/faster-whisper",
    )
    monkeypatch.setenv("INFOOS_STATE_HOME", str(tmp_path / "state"))
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"RIFF")
    cublas_package = tmp_path / "site-packages" / "nvidia" / "cublas"
    cudnn_package = tmp_path / "site-packages" / "nvidia" / "cudnn"
    (cublas_package / "lib").mkdir(parents=True)
    (cudnn_package / "lib").mkdir(parents=True)
    observed: dict[str, Any] = {}

    class PackageSpec:
        def __init__(self, package_dir: Path) -> None:
            self.submodule_search_locations = [str(package_dir)]

    package_specs = {
        "nvidia.cublas": PackageSpec(cublas_package),
        "nvidia.cudnn": PackageSpec(cudnn_package),
    }
    monkeypatch.setattr(
        "backend.application.enrichment.asr.providers.faster_whisper.importlib.util.find_spec",
        lambda name: package_specs.get(name),
    )
    monkeypatch.setenv("LD_LIBRARY_PATH", "/existing/cuda/lib")

    class Completed:
        returncode = 0
        stdout = (
            '{"status":"success","text":"\u4e2d\u6587\u8f6c\u5f55",'
            '"segments":[{"start":0.0,"end":1.0,"text":"\u4e2d\u6587\u8f6c\u5f55"}],'
            '"language":"zh","language_confidence":0.98}'
        )

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed["payload"] = kwargs["input"]
        observed["env"] = kwargs["env"]
        observed["lock_exists"] = (tmp_path / "state" / "gpu.lock").exists()
        return Completed()

    monkeypatch.setattr(
        "backend.application.enrichment.asr.providers.faster_whisper.subprocess.run",
        fake_run,
    )
    provider = FasterWhisperCudaProvider(timeout_seconds=10)

    result = provider.transcribe(
        _request(audio_path=str(audio), route_model="large-v3-turbo")
    )

    assert result.status == "success"
    assert result.text == "中文转录"
    assert result.provider == "faster-whisper-cuda"
    assert result.model == "large-v3-turbo"
    assert observed["command"][-1].endswith("faster_whisper_subprocess")
    assert '"model": "large-v3-turbo"' in observed["payload"]
    assert '"device": "cuda"' in observed["payload"]
    assert '"compute_type": "int8_float16"' in observed["payload"]
    assert json.loads(observed["payload"])["download_root"] == (
        "/var/lib/infoos/state/models/faster-whisper"
    )
    assert observed["lock_exists"] is True
    assert observed["env"]["LD_LIBRARY_PATH"].split(":") == [
        str(cublas_package / "lib"),
        str(cudnn_package / "lib"),
        "/existing/cuda/lib",
    ]


def test_faster_whisper_cuda_reuses_persistent_runtime_with_shared_gpu_lock(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("INFOOS_FASTER_WHISPER_CUDA_ENABLED", "1")
    monkeypatch.setenv("INFOOS_FASTER_WHISPER_PERSISTENT_RUNTIME_ENABLED", "1")
    monkeypatch.setenv("INFOOS_STATE_HOME", str(tmp_path / "state"))
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"RIFF")
    observed: dict[str, Any] = {}

    def fake_request(payload, *, timeout_seconds, env):
        observed["payload"] = payload
        observed["timeout_seconds"] = timeout_seconds
        observed["env"] = env
        observed["lock_exists"] = (tmp_path / "state" / "gpu.lock").exists()
        return {
            "status": "success",
            "text": "常驻模型转录",
            "segments": [{"start": 0.0, "end": 1.0, "text": "常驻模型转录"}],
            "language": "zh",
            "language_confidence": 0.99,
        }

    monkeypatch.setattr(
        "backend.application.enrichment.asr.providers.faster_whisper.request_persistent_runtime",
        fake_request,
    )

    result = FasterWhisperCudaProvider(timeout_seconds=12).transcribe(
        _request(audio_path=str(audio), route_model="large-v3-turbo")
    )

    assert result.status == "success"
    assert result.text == "常驻模型转录"
    assert observed["payload"]["model"] == "large-v3-turbo"
    assert observed["payload"]["local_files_only"] is True
    assert observed["timeout_seconds"] == 12
    assert observed["lock_exists"] is True


def test_faster_whisper_runtime_reuses_one_loaded_model(monkeypatch) -> None:
    from io import StringIO

    from backend.application.enrichment.asr.providers.faster_whisper_runtime import (
        serve,
    )

    constructor_calls: list[dict[str, Any]] = []
    transcribe_calls: list[str] = []

    class FakeWhisperModel:
        def __init__(self, _model: str, **kwargs: Any) -> None:
            constructor_calls.append(kwargs)

        def transcribe(self, audio_path: str, **_options: Any):
            transcribe_calls.append(audio_path)
            return (
                [SimpleNamespace(start=0.0, end=1.0, text="测试转录")],
                SimpleNamespace(language="zh", language_probability=0.9),
            )

    monkeypatch.setitem(
        __import__("sys").modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=FakeWhisperModel),
    )
    payload = {
        "audio_path": "/tmp/audio.wav",
        "model": "large-v3-turbo",
        "device": "cuda",
        "compute_type": "int8_float16",
        "download_root": "/models",
        "local_files_only": True,
    }
    input_stream = StringIO(
        json.dumps(payload) + "\n" + json.dumps(payload | {"audio_path": "/tmp/second.wav"}) + "\n"
    )
    output_stream = StringIO()

    assert serve(input_stream=input_stream, output_stream=output_stream) == 0

    responses = [json.loads(line) for line in output_stream.getvalue().splitlines()]
    assert [response["status"] for response in responses] == ["success", "success"]
    assert len(constructor_calls) == 1
    assert constructor_calls[0]["local_files_only"] is True
    assert transcribe_calls == ["/tmp/audio.wav", "/tmp/second.wav"]


def test_faster_whisper_subprocess_sets_download_root_only_when_non_empty(
    monkeypatch,
) -> None:
    constructor_calls: list[dict[str, Any]] = []

    class FakeWhisperModel:
        def __init__(self, _model: str, **kwargs: Any) -> None:
            constructor_calls.append(kwargs)

        def transcribe(self, _audio_path: str, **_options: Any):
            return [], SimpleNamespace(language="zh", language_probability=0.9)

    monkeypatch.setitem(
        __import__("sys").modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=FakeWhisperModel),
    )
    common = {
        "audio_path": "/tmp/audio.wav",
        "model": "large-v3-turbo",
        "device": "cuda",
        "compute_type": "int8_float16",
    }

    transcribe_faster_whisper({**common, "download_root": ""})
    transcribe_faster_whisper(
        {**common, "download_root": "/var/lib/infoos/state/models/faster-whisper"}
    )

    assert "download_root" not in constructor_calls[0]
    assert constructor_calls[0]["local_files_only"] is True
    assert constructor_calls[1]["download_root"] == (
        "/var/lib/infoos/state/models/faster-whisper"
    )
    assert constructor_calls[1]["local_files_only"] is True


def test_faster_whisper_subprocess_preserves_explicit_zero_duration_after_vad(
    monkeypatch,
) -> None:
    class FakeWhisperModel:
        def __init__(self, _model: str, **_kwargs: Any) -> None:
            pass

        def transcribe(self, _audio_path: str, **_options: Any):
            return [], SimpleNamespace(
                language="zh",
                language_probability=0.9,
                duration_after_vad=0,
            )

    monkeypatch.setitem(
        __import__("sys").modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=FakeWhisperModel),
    )

    result = transcribe_faster_whisper(
        {
            "audio_path": "/tmp/audio.wav",
            "model": "large-v3-turbo",
            "device": "cuda",
            "compute_type": "int8_float16",
        }
    )

    assert result["status"] == "success"
    assert result["text"] == ""
    assert result["duration_after_vad"] == 0.0
