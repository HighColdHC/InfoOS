#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"

if [[ ! -x "${PYTHON_BIN}" ]] || ! "${PYTHON_BIN}" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
then
  PY311_BIN="${PYTHON311_BIN:-$(command -v python3.11 || command -v python3 || true)}"
  if [[ -z "${PY311_BIN}" ]]; then
    echo "error: Python 3.11+ is required. Set PYTHON311_BIN or install python3.11." >&2
    exit 1
  fi
  rm -rf "${ROOT_DIR}/.venv"
  "${PY311_BIN}" -m venv "${ROOT_DIR}/.venv"
fi

PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
echo "installing InfoOS development dependencies"
if "${PYTHON_BIN}" -m pip --version >/dev/null 2>&1; then
  "${PYTHON_BIN}" -m pip install --upgrade pip setuptools wheel
  "${PYTHON_BIN}" -m pip install -e "${ROOT_DIR}[dev,web]"
elif command -v uv >/dev/null 2>&1; then
  uv pip install --python "${PYTHON_BIN}" -e "${ROOT_DIR}[dev,web]"
else
  echo "error: this virtual environment has no pip module and uv is not on PATH. Run ${PYTHON_BIN} -m ensurepip --upgrade, or install uv and add it to PATH; then retry make install." >&2
  exit 1
fi

echo "done"
