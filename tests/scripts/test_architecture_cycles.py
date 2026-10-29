from __future__ import annotations

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"


def _import_graph() -> dict[str, set[str]]:
    modules = {
        ".".join(path.relative_to(PROJECT_ROOT).with_suffix("").parts): path
        for path in BACKEND_ROOT.rglob("*.py")
    }
    graph = {module: set() for module in modules}
    for module, path in modules.items():
        package = module.split(".")[:-1]
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            imported = node.module or ""
            if node.level:
                imported = ".".join(package[: len(package) - node.level + 1] + ([imported] if imported else []))
            if imported in modules:
                graph[module].add(imported)
            for name in node.names:
                child = f"{imported}.{name.name}" if imported else name.name
                if child in modules:
                    graph[module].add(child)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                graph[module].update(alias.name for alias in node.names if alias.name in modules)
    return graph


def _reachable(graph: dict[str, set[str]], start: str, target: str) -> bool:
    pending = [start]
    visited: set[str] = set()
    while pending:
        current = pending.pop()
        if current == target:
            return True
        if current in visited:
            continue
        visited.add(current)
        pending.extend(graph.get(current, set()) - visited)
    return False


def test_remediated_module_groups_do_not_form_import_cycles() -> None:
    graph = _import_graph()
    formerly_cyclic_pairs = (
        ("backend.application.workflows.fetch", "backend.application.workflows.acquisition"),
        ("backend.application.tasks.executor", "backend.application.tasks.processor"),
        ("backend.read_models.task_manager", "backend.read_models.task_batches"),
    )
    for left, right in formerly_cyclic_pairs:
        assert not (_reachable(graph, left, right) and _reachable(graph, right, left)), (left, right)


def test_media_router_owns_platform_strategy_direction() -> None:
    graph = _import_graph()
    router = "backend.application.enrichment.media.download"
    strategies = {
        "backend.application.enrichment.media.douyin",
        "backend.application.enrichment.media.gallery",
        "backend.application.enrichment.media.xiaohongshu",
        "backend.application.enrichment.media.youtube",
    }
    artifacts = "backend.application.enrichment.media.artifacts"
    assert strategies <= graph[router]
    assert all(artifacts in graph[strategy] for strategy in strategies)
    assert all(router not in graph[strategy] for strategy in strategies)
