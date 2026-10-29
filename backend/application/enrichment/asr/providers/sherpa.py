from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from backend.application.enrichment.asr.models import (
    AsrRequest,
    AsrResult,
    AsrStatus,
    ProviderAvailability,
)
from backend.infrastructure.storage.paths import resolve_data_dir


class SherpaOnnxProvider:
    provider_id = "sherpa-onnx"
    resource_profile = "local-low-resource"

    def __init__(
        self,
        *,
        base_dir: str | Path | None = None,
        executable: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.base_dir = Path(base_dir) if base_dir is not None else None
        self.executable = executable or os.environ.get("INFOOS_SHERPA_ONNX_EXECUTABLE") or "sherpa-onnx-offline"
        self.timeout_seconds = timeout_seconds or _positive_float_env(
            "INFOOS_SHERPA_ONNX_TIMEOUT_SECONDS",
            default=1800.0,
        )

    def is_available(self) -> ProviderAvailability:
        if shutil.which(self.executable):
            return ProviderAvailability.available(provider_id=self.provider_id)
        if _python_runtime_available():
            return ProviderAvailability.available(provider_id=self.provider_id)
        return ProviderAvailability.blocked(
            provider_id=self.provider_id,
            reason="blocked_sherpa_onnx_missing",
            message=f"{self.executable} executable and sherpa_onnx Python runtime were not found",
        )

    def supports(self, request: AsrRequest) -> bool:
        model_dir = self._model_dir(request)
        if not model_dir.is_dir():
            return False
        if (request.route_model or "").startswith("sensevoice"):
            return (model_dir / "model.int8.onnx").is_file() and (model_dir / "tokens.txt").is_file()
        return False

    def transcribe(self, request: AsrRequest) -> AsrResult:
        model_dir = self._model_dir(request)
        started = time.monotonic()
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "backend.application.enrichment.asr.providers.sherpa_subprocess",
                ],
                input=json.dumps(
                    {
                        "model_dir": str(model_dir),
                        "audio_path": str(Path(request.audio_path)),
                        "num_threads": request.max_cpu_threads or 2,
                    }
                ),
                text=True,
                capture_output=True,
                check=False,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return _terminal_result(
                request,
                status="blocked",
                error="blocked_asr_timeout",
            )
        except OSError as exc:
            return _terminal_result(
                request,
                status="failed",
                error="failed_asr_subprocess_start",
                quality_flags=[type(exc).__name__],
            )

        if completed.returncode != 0:
            killed = completed.returncode in {-signal.SIGKILL, 128 + signal.SIGKILL}
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
        if not isinstance(payload, dict):
            return _terminal_result(
                request,
                status="failed",
                error="failed_asr_subprocess_protocol",
            )
        if str(payload.get("status") or "") != "success":
            return _terminal_result(
                request,
                status="failed",
                error=str(payload.get("error") or "failed_asr_runtime_error"),
                quality_flags=[str(flag) for flag in payload.get("quality_flags") or []],
            )

        text = str(payload.get("text") or "").strip()
        raw_segments = payload.get("segments")
        segments = (
            [segment for segment in raw_segments if isinstance(segment, dict)]
            if isinstance(raw_segments, list)
            else []
        )
        elapsed = max(time.monotonic() - started, 0.001)
        return AsrResult(
            text=text,
            segments=segments,
            language=request.detected_language or request.language_hint or "unknown",
            language_confidence=request.language_confidence,
            provider=self.provider_id,
            model=request.route_model or "",
            duration_seconds=request.duration_seconds,
            rtf=elapsed / request.duration_seconds if request.duration_seconds else None,
        )

    def _model_dir(self, request: AsrRequest) -> Path:
        explicit_root = os.environ.get("INFOOS_ASR_MODEL_DIR") or os.environ.get("INFOOS_SHERPA_ONNX_MODEL_DIR")
        if explicit_root:
            root = Path(explicit_root)
        elif self.base_dir is not None:
            root = resolve_data_dir(self.base_dir) / "asr_models"
        else:
            root = Path.home() / ".infoos" / "data" / "asr_models"
        names = request.model_profile.directory_names if request.model_profile else ()
        candidates = [name for name in names if name]
        if request.route_model:
            candidates.append(request.route_model)
        for name in candidates:
            path = root / name
            if path.is_dir():
                return path
        return root / (request.route_model or "")


def _python_runtime_available() -> bool:
    try:
        import sherpa_onnx  # noqa: F401

        return True
    except ImportError:
        return False


def _positive_float_env(name: str, *, default: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


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
        provider=SherpaOnnxProvider.provider_id,
        model=request.route_model or "",
        quality_flags=quality_flags or [],
    )
