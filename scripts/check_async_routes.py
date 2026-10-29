#!/usr/bin/env python3
"""Require an explicit allowlist entry for every asynchronous API route.

Synchronous handlers run in FastAPI's thread pool. Only handlers that need to
await a request body or WebSocket belong here; their blocking work must still
be offloaded with run_in_threadpool.
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path


ASYNC_ROUTE_ALLOWLIST = frozenset({
    # Reads the bounded capture request body.
    "backend/api/routes/capture.py:submit_item",
    # Reads an optional JSON request body before enqueueing sources.
    "backend/api/routes/runs.py:api_source_run_batch",
    # Awaits bidirectional WebSocket traffic.
    "backend/api/routes/browser.py:browser_console_websocket",
    # Read the account form body; the account change itself runs in the threadpool.
    "backend/api/routes/lan_account.py:change_password",
    "backend/api/routes/lan_account.py:change_username",
})


def iter_python(root: Path):
    api_root = root / "backend" / "api"
    yield from sorted({*api_root.glob("*.py"), *(api_root / "routes").rglob("*.py")})


def _is_router_decorator(node: ast.expr) -> bool:
    target = node.func if isinstance(node, ast.Call) else node
    return (
        isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == "router"
    )


def check_file(path: Path, root: Path) -> tuple[list[tuple[str, int]], list[str]]:
    relative = path.relative_to(root).as_posix()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        return [], [f"{relative}: SyntaxError at line {exc.lineno}: {exc.msg}"]
    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        if not any(_is_router_decorator(decorator) for decorator in node.decorator_list):
            continue
        key = f"{relative}:{node.name}"
        if key not in ASYNC_ROUTE_ALLOWLIST:
            violations.append((key, node.lineno))
    return violations, []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="Repository root (default: cwd).")
    root = Path(parser.parse_args(argv).root).resolve()
    violations = []
    parse_errors = []
    scanned = 0
    for path in iter_python(root):
        scanned += 1
        file_violations, file_errors = check_file(path, root)
        violations.extend(file_violations)
        parse_errors.extend(file_errors)
    if parse_errors:
        print(f"async-routes-check: {len(parse_errors)} parse error(s):")
        for error in parse_errors:
            print(f"  {error}")
    if violations:
        print(f"async-routes-check: scanned {scanned} files; {len(violations)} violation(s):")
        for key, lineno in sorted(violations):
            print(f"  {key} (line {lineno}): use def or document required await in the allowlist")
    elif not parse_errors:
        print(f"async-routes-check: scanned {scanned} files; OK.")
    return 1 if violations or parse_errors else 0


if __name__ == "__main__":
    sys.exit(main())
