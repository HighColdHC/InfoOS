"""Tests for scripts/check_file_size.py file-size guard.

These tests verify the file-size checking logic that enforces AGENTS.md「代码结构」
thresholds. They are intentionally self-contained: they create temp files
rather than scanning the real repo, so they don't break as the codebase
changes.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "check_file_size.py"


def _run(repo: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(repo), *extra],
        capture_output=True,
        text=True,
    )


def _write(path: Path, lines: int, noqa: bool = False, prefix: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = ""
    if noqa:
        header = "# noqa: file-size - reason: too coupled to split\n"
    elif prefix:
        header = prefix
    body = "\n".join(f"# line {i}" for i in range(lines))
    path.write_text(header + body + "\n")


# ---------- classification tests ----------


def test_detects_oversized_component(tmp_path: Path) -> None:
    """A .tsx file over 400 lines in frontend/src must be flagged."""
    f = tmp_path / "frontend" / "src" / "Big.tsx"
    _write(f, 450)
    result = _run(tmp_path)
    assert result.returncode != 0
    assert "frontend/src/Big.tsx" in result.stdout or "Big.tsx" in result.stdout
    assert "component" in result.stdout.lower()
    assert "400" in result.stdout  # threshold printed


def test_passes_small_component(tmp_path: Path) -> None:
    """A .tsx file under 250 lines in frontend/src must pass."""
    f = tmp_path / "frontend" / "src" / "Small.tsx"
    _write(f, 100)
    result = _run(tmp_path)
    assert result.returncode == 0, result.stdout
    assert "ERROR" not in result.stdout


def test_detects_oversized_controller(tmp_path: Path) -> None:
    """backend/api/routes/foo.py over 250 lines must be flagged."""
    f = tmp_path / "backend" / "api" / "routes" / "cards.py"
    _write(f, 300)
    result = _run(tmp_path)
    assert result.returncode != 0
    assert "cards.py" in result.stdout
    assert "controller" in result.stdout.lower()
    assert "250" in result.stdout


def test_detects_oversized_repository(tmp_path: Path) -> None:
    """backend/.../storage/repositories.py over 500 lines must be flagged."""
    f = tmp_path / "backend" / "infrastructure" / "storage" / "repositories.py"
    _write(f, 600)
    result = _run(tmp_path)
    assert result.returncode != 0
    assert "repositories.py" in result.stdout
    assert "repository" in result.stdout.lower()
    assert "500" in result.stdout


def test_detects_oversized_service(tmp_path: Path) -> None:
    """backend/.../services/foo_service.py over 600 lines must be flagged."""
    f = tmp_path / "backend" / "application" / "services" / "card_service.py"
    _write(f, 700)
    result = _run(tmp_path)
    assert result.returncode != 0
    assert "card_service.py" in result.stdout
    assert "service" in result.stdout.lower()
    assert "600" in result.stdout


# ---------- noqa / skip tests ----------


def test_noqa_marker_grants_exemption(tmp_path: Path) -> None:
    """A file with `# noqa: file-size` in first 5 lines is exempt."""
    f = tmp_path / "frontend" / "src" / "Huge.tsx"
    _write(f, 800, noqa=True)
    result = _run(tmp_path)
    assert result.returncode == 0, result.stdout
    assert "Huge.tsx" not in result.stdout or "skip" in result.stdout.lower()


def test_skips_unrelated_paths(tmp_path: Path) -> None:
    """node_modules, dist, build, .worktrees, __pycache__ must be skipped."""
    for rel in [
        "node_modules/pkg/index.js",
        "dist/bundle.js",
        "build/output.py",
        ".worktrees/other/file.py",
        "frontend/node_modules/x.tsx",
        "backend/__pycache__/mod.py",
    ]:
        _write(tmp_path / rel, 999)
    result = _run(tmp_path)
    assert result.returncode == 0, result.stdout
    assert "ERROR" not in result.stdout


# ---------- output format ----------


def test_marks_thresholds_in_message(tmp_path: Path) -> None:
    """Each violation message should include kind, threshold, actual count."""
    f = tmp_path / "backend" / "api" / "routes" / "foo.py"
    _write(f, 999)
    result = _run(tmp_path)
    out = result.stdout + result.stderr
    # Should mention "controller" kind, "250" threshold, "999" actual
    assert "controller" in out.lower()
    assert "250" in out
    assert "999" in out
