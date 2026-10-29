"""Task table writes belong to storage, even when import checks cannot see SQL."""
from __future__ import annotations

import ast
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
WRITE = re.compile(
    r"\b(?:insert\s+(?:or\s+\w+\s+)?into|update|delete\s+from)\s+"
    r"(?:main\.)?(?:tasks|task_events)\b", re.IGNORECASE,
)
SCANNED_LAYERS = ("application", "read_models", "api", "workers")
READ_MODEL_SELECT_ALLOWLIST = frozenset({
    "card_preview.py", "collector_status.py", "research_runs.py",
    "source_management_page.py", "task_manager_context.py", "task_queue_tracking.py",
})
TASK_SELECT = re.compile(r"\b(?:from|join)\s+tasks\b", re.IGNORECASE)


def _sql_literals(tree: ast.AST) -> list[tuple[int, str]]:
    """Collect literal fragments, including f-string fragments and concatenations."""
    return [(node.lineno, node.value) for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)]


def test_task_writes_only_in_storage() -> None:
    violations: list[str] = []
    for layer in SCANNED_LAYERS:
        for path in (ROOT / "backend" / layer).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for line, literal in _sql_literals(tree):
                if WRITE.search(literal):
                    violations.append(f"{path.relative_to(ROOT)}:{line}")
                if layer == "read_models" and TASK_SELECT.search(literal):
                    if path.name not in READ_MODEL_SELECT_ALLOWLIST:
                        violations.append(f"unlisted read model {path.relative_to(ROOT)}:{line}")
    assert not violations, "task SQL outside storage: " + ", ".join(violations)


def test_storage_cannot_import_application() -> None:
    violations: list[str] = []
    for path in (ROOT / "backend" / "infrastructure" / "storage").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            elif isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            else:
                continue
            if any(module == "backend.application" or module.startswith("backend.application.")
                   for module in modules):
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not violations, "storage imports application: " + ", ".join(violations)


def test_guard_recognizes_task_table_writes() -> None:
    for sql in (
        "INSERT INTO tasks (task_id) VALUES (?)",
        "update\n task_events set status = ?",
        "delete from main.tasks where task_id = ?",
    ):
        assert WRITE.search(sql)
    assert not WRITE.search("select * from tasks")
