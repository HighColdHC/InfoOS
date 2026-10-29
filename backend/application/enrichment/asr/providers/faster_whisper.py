"""Optional faster-whisper CUDA provider executed in an isolated process."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from backend.application.enrichment.asr.models import (
    AsrRequest,
    AsrResult,
    AsrStatus,
    ProviderAvailability,
)
from backend.application.enrichment.asr.language import normalize_language
from backend.application.enrichment.asr.providers.faster_whisper_runtime_client import (
    request_persistent_runtime,
)
from backend.infrastructure.runtime.environment import (
    faster_whisper_cuda_enabled,
    faster_whisper_download_root,
)
from backend.infrastructure.runtime.gpu_lock import GpuLockTimeoutError, gpu_lock


class FasterWhisperCudaProvider:
    provider_id = "faster-whisper-cuda"
    resource_profile = "local-gpu"
    model_id = "large-v3-turbo"
    device = "cuda"
    compute_type = "int8_float16"

    def __init__(self, *, timeout_seconds: float | None = None) -> None:
        self.timeout_seconds = timeout_seconds or _positive_float_env(
            "INFOOS_FASTER_WHISPER_TIMEOUT_SECONDS", default=3600.0
        )

    def is_available(self) -> ProviderAvailability:
        if not faster_whisper_cuda_enabled():
            return ProviderAvailability.blocked(
                provider_id=self.provider_id,
                reason="blocked_faster_whisper_cuda_disabled",
                message="Set INFOOS_FASTER_WHISPER_CUDA_ENABLED=1 to enable CUDA ASR",
            )
        if importlib.util.find_spec("faster_whisper") is None:
            return ProviderAvailability.blocked(
                provider_id=self.provider_id,
                reason="blocked_faster_whisper_cuda_missing",
                message="The optional faster-whisper runtime is not installed",
            )
        return ProviderAvailability.available(provider_id=self.provider_id)

    def supports(self, request: AsrRequest) -> bool:
        return request.route_model == self.model_id and Path(request.audio_path).is_file()

    def transcribe(self, request: AsrRequest) -> AsrResult:
        started = time.monotonic()
        request_payload = {
            "audio_path": str(Path(request.audio_path)),
            "model": self.model_id,
            "device": self.device,
            "compute_type": self.compute_type,
            "language": _whisper_language(request),
            "download_root": faster_whisper_download_root(),
            "local_files_only": True,
        }
        try:
            with gpu_lock():
                if _persistent_runtime_enabled():
                    payload = request_persistent_runtime(
                        request_payload,
                        timeout_seconds=self.timeout_seconds,
                        env=_cuda_subprocess_env(),
                    )
                else:
                    completed = subprocess.run(
                        [
                            sys.executable,
                            "-m",
                            "backend.application.enrichment.asr.providers.faster_whisper_subprocess",
                        ],
                        input=json.dumps(request_payload),
                        text=True,
                        capture_output=True,
                        check=False,
                        timeout=self.timeout_seconds,
                        env=_cuda_subprocess_env(),
                    )
                    if completed.returncode != 0:
                        killed = completed.returncode in {
                            -signal.SIGKILL,
                            128 + signal.SIGKILL,
                        }
                        return _terminal_result(
                            request,
                            status="failed",
                            error=(
                                "failed_asr_subprocess_sigkill"
                                if killed
                                else "failed_asr_subprocess_exit"
                            ),
                            quality_flags=[f"returncode:{completed.returncode}"],
                        )
                    try:
                        payload = json.loads(completed.stdout)
                    except json.JSONDecodeError:
                        return _terminal_result(
                            request,
                            status="failed",
                            error="failed_asr_subprocess_protocol",
                        )
        except GpuLockTimeoutError:
            return _terminal_result(request, status="blocked", error="blocked_gpu_lock_timeout")
        except subprocess.TimeoutExpired:
            return _terminal_result(request, status="blocked", error="blocked_asr_timeout")
        except RuntimeError as exc:
            return _terminal_result(
                request,
                status="failed",
                error="failed_asr_subprocess_exit",
                quality_flags=[type(exc).__name__],
            )
        except OSError as exc:
            return _terminal_result(
                request,
                status="failed",
                error="failed_asr_subprocess_start",
                quality_flags=[type(exc).__name__],
            )
        if not isinstance(payload, dict):
            return _terminal_result(
                request, status="failed", error="failed_asr_subprocess_protocol"
            )
        if str(payload.get("status") or "") != "success":
            return _terminal_result(
                request,
                status="failed",
                error=str(payload.get("error") or "failed_asr_runtime_error"),
                quality_flags=[str(flag) for flag in payload.get("quality_flags") or []],
            )
        elapsed = max(time.monotonic() - started, 0.001)
        raw_segments = payload.get("segments")
        return AsrResult(
            text=str(payload.get("text") or "").strip(),
            segments=(
                [segment for segment in raw_segments if isinstance(segment, dict)]
                if isinstance(raw_segments, list)
                else []
            ),
            language=str(payload.get("language") or request.detected_language or request.language_hint or "unknown"),
            language_confidence=_float_or_none(payload.get("language_confidence")),
            provider=self.provider_id,
            model=self.model_id,
            duration_seconds=request.duration_seconds,
            duration_after_vad=_float_or_none(payload.get("duration_after_vad")),
            rtf=elapsed / request.duration_seconds if request.duration_seconds else None,
        )


def _terminal_result(
    request: AsrRequest,
    *,
    status: AsrStatus,
    error: str,
    quality_flags: list[str] | None = None,
) -> AsrResult:
    return AsrResult(
        status=status,
        error=error,
        provider=FasterWhisperCudaProvider.provider_id,
        model=request.route_model or FasterWhisperCudaProvider.model_id,
        quality_flags=quality_flags or [],
    )


def _positive_float_env(name: str, *, default: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _persistent_runtime_enabled() -> bool:
    return os.environ.get(
        "INFOOS_FASTER_WHISPER_PERSISTENT_RUNTIME_ENABLED",
        "1",
    ).strip().lower() in {"1", "true", "yes", "on"}


def _float_or_none(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _whisper_language(request: AsrRequest) -> str | None:
    language = normalize_language(request.detected_language or request.language_hint)
    if language == "yue":
        return "zh"
    if language in {"unknown", "mixed", "und"}:
        return None
    return language


def _cuda_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    library_dirs = _cuda_library_dirs()
    existing = env.get("LD_LIBRARY_PATH", "").strip()
    parts = [str(path) for path in library_dirs]
    if existing:
        parts.append(existing)
    if parts:
        env["LD_LIBRARY_PATH"] = os.pathsep.join(parts)
    return env


def _cuda_library_dirs() -> list[Path]:
    discovered: list[Path] = []
    for package_name in ("nvidia.cublas", "nvidia.cudnn"):
        try:
            spec = importlib.util.find_spec(package_name)
        except (ImportError, ValueError):
            continue
        if spec is None:
            continue
        package_dirs = list(spec.submodule_search_locations or [])
        if not package_dirs and spec.origin:
            package_dirs = [str(Path(spec.origin).parent)]
        for package_dir in package_dirs:
            library_dir = Path(package_dir) / "lib"
            if library_dir.is_dir() and library_dir not in discovered:
                discovered.append(library_dir)
    return discovered
