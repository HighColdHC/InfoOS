"""Process-isolated client for the long-lived faster-whisper CUDA runtime."""
from __future__ import annotations

import atexit
import json
import selectors
import subprocess
import sys
import threading
from typing import Any


_RUNTIME_MODULE = (
    "backend.application.enrichment.asr.providers.faster_whisper_runtime"
)


class FasterWhisperRuntimeClient:
    def __init__(self) -> None:
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()

    def request(
        self,
        payload: dict[str, Any],
        *,
        timeout_seconds: float,
        env: dict[str, str],
    ) -> dict[str, Any]:
        with self._lock:
            process = self._ensure_process(env)
            try:
                process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
                process.stdin.flush()
                line = self._readline(process, timeout_seconds)
            except (OSError, subprocess.TimeoutExpired):
                self.stop()
                raise
            if not line:
                returncode = process.poll()
                self.stop()
                raise RuntimeError(
                    f"faster-whisper runtime exited with status {returncode}"
                )
            try:
                result = json.loads(line)
            except json.JSONDecodeError as exc:
                self.stop()
                raise RuntimeError(
                    "faster-whisper runtime returned invalid JSON"
                ) from exc
            if not isinstance(result, dict):
                self.stop()
                raise RuntimeError(
                    "faster-whisper runtime returned a non-object response"
                )
            return result

    def _ensure_process(
        self, env: dict[str, str]
    ) -> subprocess.Popen[str]:
        if self._process is not None and self._process.poll() is None:
            return self._process
        self.stop()
        self._process = subprocess.Popen(
            [sys.executable, "-m", _RUNTIME_MODULE],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
        return self._process

    def _readline(
        self,
        process: subprocess.Popen[str],
        timeout_seconds: float,
    ) -> str:
        selector = selectors.DefaultSelector()
        try:
            selector.register(process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=max(timeout_seconds, 0.001)):
                raise subprocess.TimeoutExpired(
                    process.args,
                    timeout_seconds,
                )
            return process.stdout.readline()
        finally:
            selector.close()

    def stop(self) -> None:
        process, self._process = self._process, None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)


_CLIENT = FasterWhisperRuntimeClient()
atexit.register(_CLIENT.stop)


def request_persistent_runtime(
    payload: dict[str, Any],
    *,
    timeout_seconds: float,
    env: dict[str, str],
) -> dict[str, Any]:
    return _CLIENT.request(
        payload,
        timeout_seconds=timeout_seconds,
        env=env,
    )
