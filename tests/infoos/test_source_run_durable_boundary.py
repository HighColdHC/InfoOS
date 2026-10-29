"""Production source runs stay on the durable task boundary."""
from __future__ import annotations

import ast
from importlib.util import resolve_name
from pathlib import Path

from backend.application.workflows.source_batch import main as source_batch_main


def test_app_facing_entrypoints_do_not_import_legacy_sync_run_once():
    """The only sync run-once callable is a diagnostic/CLI compatibility path."""
    project_root = Path(__file__).resolve().parents[2]
    forbidden = "backend.application.workflows.run_once"
    violations = []
    for path in (project_root / "backend/api").rglob("*.py"):
        package = ".".join(path.relative_to(project_root).parts[:-1])
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                module = resolve_name("." * node.level + (node.module or ""), package)
                imports = [module, *(f"{module}.{alias.name}" for alias in node.names)]
            else:
                continue
            if any(name == forbidden or name.startswith(forbidden + ".") for name in imports):
                violations.append(f"{path.relative_to(project_root)}:{node.lineno}")
    assert not violations, "synchronous collection imported by API: " + ", ".join(violations)


def test_source_batch_cli_only_submits_durable_work(tmp_path, monkeypatch, capsys):
    submitted = []

    def fake_enqueue(base_dir, *, limit):
        submitted.append({"base_dir": base_dir, "limit": limit})
        return {"batch_id": "source_run_batch", "task_ids": ["fetch_rss"]}

    monkeypatch.setattr(
        "backend.application.workflows.source_batch.enqueue_source_batch",
        fake_enqueue,
    )

    assert source_batch_main(["--base-dir", str(tmp_path), "--limit", "7"]) == 0
    assert submitted == [{"base_dir": tmp_path, "limit": 7}]
    output = capsys.readouterr().out
    assert '"status": "submitted"' in output
    assert '"consumer": "backend.workers.task_worker"' in output
