"""Tests for the asynchronous route allowlist guard."""
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_async_routes.py"


def _run(root: Path):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root)],
        capture_output=True, text=True,
    )


def _write(root: Path, relative: str, content: str):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_current_tree_passes():
    result = _run(REPO_ROOT)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


@pytest.mark.parametrize("relative", ["backend/api/new.py", "backend/api/routes/nested/new.py"])
@pytest.mark.parametrize("method", ["get", "post", "put", "patch", "delete", "head", "options", "trace", "api_route", "websocket"])
def test_rejects_unlisted_async_route(tmp_path, relative, method):
    _write(tmp_path, relative, f'@router.{method}("/new")\nasync def new_route():\n    pass\n')
    result = _run(tmp_path)
    assert result.returncode == 1
    assert f"{relative}:new_route" in result.stdout


def test_allows_sync_routes_and_async_helpers(tmp_path):
    _write(tmp_path, "backend/api/routes/example.py", '''
@router.get("/sync")
def sync_route():
    pass

async def helper():
    pass
''')
    assert _run(tmp_path).returncode == 0


@pytest.mark.parametrize("relative,name,method", [
    ("backend/api/routes/capture.py", "submit_item", "post"),
    ("backend/api/routes/browser.py", "browser_console_websocket", "websocket"),
])
def test_allows_documented_async_routes(tmp_path, relative, name, method):
    _write(tmp_path, relative, f'@router.{method}("/allowed")\nasync def {name}():\n    pass\n')
    assert _run(tmp_path).returncode == 0


def test_allowlist_requires_matching_file_and_name(tmp_path):
    _write(tmp_path, "backend/api/routes/other.py", '@router.post("/x")\nasync def submit_item():\n    pass\n')
    assert _run(tmp_path).returncode == 1


def test_reports_syntax_errors(tmp_path):
    _write(tmp_path, "backend/api/routes/broken.py", "async def broken(:\n    pass\n")
    result = _run(tmp_path)
    assert result.returncode == 1
    assert "SyntaxError" in result.stdout
