from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from backend.infrastructure.runtime.gpu_lock import gpu_lock


def test_gpu_lock_serializes_independent_processes(tmp_path, monkeypatch) -> None:
    state_home = tmp_path / "state"
    monkeypatch.setenv("INFOOS_STATE_HOME", str(state_home))
    child_env = os.environ.copy()
    child_env["INFOOS_STATE_HOME"] = str(state_home)
    child_env["PYTHONPATH"] = str(Path(__file__).parents[3])

    with gpu_lock(timeout_seconds=1):
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "from backend.infrastructure.runtime.gpu_lock import gpu_lock; "
                    "from backend.infrastructure.runtime.gpu_lock import GpuLockTimeoutError; "
                    "\ntry:\n"
                    "  with gpu_lock(timeout_seconds=0.05): pass\n"
                    "except GpuLockTimeoutError:\n"
                    "  print('timed-out')\n"
                ),
            ],
            cwd=str(tmp_path),
            env=child_env,
            text=True,
            capture_output=True,
            check=False,
        )

    assert completed.returncode == 0
    assert completed.stdout.strip() == "timed-out"
