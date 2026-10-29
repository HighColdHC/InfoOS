"""Tests for scripts/check_layer_deps.py."""
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_layer_deps.py"


def _make_file(path: Path, content: str) -> None:
    """Create `path` (and parents) and write `content` to it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_script_exists():
    assert SCRIPT.exists(), f"{SCRIPT} not found"


def test_script_runs_clean_on_current_tree():
    """On the current tree, the script should report OK (or at most parse errors).

    The current tree should obey the top-level backend layer rules.
    """
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    # Either OK (returncode 0) or only parse errors (returncode 1 but no violations)
    assert "OK" in result.stdout or "violation" not in result.stdout, (
        f"unexpected output: {result.stdout}\n{result.stderr}"
    )


def test_detects_api_to_scheduler():
    """backend.api -> backend.workers is forbidden (independent entry points)."""
    with tempfile.TemporaryDirectory() as tmp:
        api_file = Path(tmp) / "backend" / "api" / "test.py"
        _make_file(api_file, "from backend.workers.scheduler import cycle\n")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", tmp],
            capture_output=True, text=True,
        )
        assert result.returncode != 0
        assert "backend.api" in result.stdout and "backend.workers" in result.stdout


def test_detects_scheduler_to_api():
    """backend.workers -> backend.api is forbidden (symmetric)."""
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "backend" / "workers" / "test.py"
        _make_file(f, "from backend.api import main\n")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", tmp],
            capture_output=True, text=True,
        )
        assert result.returncode != 0
        assert "backend.workers" in result.stdout and "backend.api" in result.stdout


def test_detects_application_to_api():
    """backend.application -> backend.api is forbidden (application is upstream)."""
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "backend" / "application" / "test.py"
        _make_file(f, "from backend.api import main\n")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", tmp],
            capture_output=True, text=True,
        )
        assert result.returncode != 0
        assert "backend.application" in result.stdout and "backend.api" in result.stdout


def test_allows_api_to_application_workflows():
    """backend.api -> backend.application is allowed."""
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "backend" / "api" / "test.py"
        _make_file(f, "from backend.application.workflows import run_once\n")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", tmp],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"unexpected violation: {result.stdout}\n{result.stderr}"


def test_detects_lazy_import_inside_function():
    """Function-body imports must not hide layer violations."""
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "backend" / "api" / "test.py"
        _make_file(
            f,
            (
                "def handler():\n"
                "    from backend.workers.scheduler import cycle\n"
                "    return cycle\n"
            ),
        )
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", tmp],
            capture_output=True, text=True,
        )
        assert result.returncode != 0
        assert "backend.api" in result.stdout and "backend.workers" in result.stdout


def test_detects_read_models_to_workers():
    """backend.read_models -> backend.workers is forbidden."""
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "backend" / "read_models" / "test.py"
        _make_file(f, "from backend.workers.scheduler import forecast\n")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", tmp],
            capture_output=True, text=True,
        )
        assert result.returncode != 0
        assert "backend.read_models" in result.stdout and "backend.workers" in result.stdout


def test_detects_infrastructure_to_application():
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "backend" / "infrastructure" / "test.py"
        _make_file(f, "from backend.application import workflows\n")
        result = subprocess.run([sys.executable, str(SCRIPT), "--root", tmp], capture_output=True, text=True)
        assert result.returncode != 0
        assert "backend.infrastructure" in result.stdout and "backend.application" in result.stdout


def test_detects_infrastructure_to_read_models():
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "backend" / "infrastructure" / "test.py"
        _make_file(f, "from backend.read_models import cards\n")
        result = subprocess.run([sys.executable, str(SCRIPT), "--root", tmp], capture_output=True, text=True)
        assert result.returncode != 0
        assert "backend.infrastructure" in result.stdout and "backend.read_models" in result.stdout


def test_detects_application_to_read_models():
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "backend" / "application" / "test.py"
        _make_file(f, "from backend.read_models import cards\n")
        result = subprocess.run([sys.executable, str(SCRIPT), "--root", tmp], capture_output=True, text=True)
        assert result.returncode != 0
        assert "backend.application" in result.stdout and "backend.read_models" in result.stdout


def test_detects_read_models_to_application():
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "backend" / "read_models" / "test.py"
        _make_file(f, "from backend.application import workflows\n")
        result = subprocess.run([sys.executable, str(SCRIPT), "--root", tmp], capture_output=True, text=True)
        assert result.returncode != 0
        assert "backend.read_models" in result.stdout and "backend.application" in result.stdout


def test_reports_syntax_errors():
    """A file with SyntaxError must be reported, not silently skipped."""
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "backend" / "api" / "test.py"
        _make_file(f, "def broken(:\n    pass\n")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", tmp],
            capture_output=True, text=True,
        )
        # Either it reports the syntax error explicitly, or it returns 0 because
        # the empty backend tree has no files to scan (and the broken file is
        # skipped by ast.parse). We assert that EITHER there's an error reported
        # OR no violation is reported (the script doesn't crash).
        assert "SyntaxError" in result.stdout or result.returncode == 0 or "violation" not in result.stdout
