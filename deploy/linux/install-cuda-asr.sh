#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${INFOOS_APP_DIR:-/opt/infoos/app}"
STATE_HOME="${INFOOS_STATE_HOME:-/var/lib/infoos/state}"
MODEL_ROOT="${INFOOS_FASTER_WHISPER_DOWNLOAD_ROOT:-$STATE_HOME/models/faster-whisper}"
PYTHON="$APP_DIR/.venv/bin/python"

[[ -x "$PYTHON" ]] || {
  echo "InfoOS Python is missing: $PYTHON" >&2
  exit 1
}
command -v nvidia-smi >/dev/null || {
  echo "nvidia-smi is required before enabling CUDA ASR" >&2
  exit 1
}
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader

mkdir -p "$MODEL_ROOT"
"$PYTHON" -m pip install --upgrade "$APP_DIR[asr-cuda]"

export INFOOS_FASTER_WHISPER_DOWNLOAD_ROOT="$MODEL_ROOT"
"$PYTHON" - <<'PY'
import os
from pathlib import Path

from faster_whisper import WhisperModel

root = Path(os.environ["INFOOS_FASTER_WHISPER_DOWNLOAD_ROOT"])
model = WhisperModel(
    "large-v3-turbo",
    device="cuda",
    compute_type="int8_float16",
    download_root=str(root),
)
del model
print(f"faster-whisper CUDA model ready: {root}")
PY

echo "Add the INFOOS_FASTER_WHISPER_* variables from deploy/linux/infoos.env.example"
echo "to /etc/infoos/infoos.env, then restart infoos-worker."
