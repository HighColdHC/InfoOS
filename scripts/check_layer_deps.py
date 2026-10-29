#!/usr/bin/env python3
"""Layer dependency guard for the backend package.

Enforces the current backend structure:
- backend.api must not import backend.workers.
- backend.workers must not import backend.api.
- backend.application must not import backend.api or backend.workers.
- backend.domain must not import backend.api or backend.workers.
- backend.domain must not import backend.application, backend.infrastructure, or backend.read_models.
- backend.read_models must not import backend.workers.
- backend.infrastructure must not import backend.application or backend.read_models.
- backend.application and backend.read_models must not import each other.

All imports are checked, including lazy/function-body imports. Lazy imports are
valid for startup cost or optional dependencies, not for crossing forbidden
architecture boundaries.
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path


LAYER_ROOTS = ("api", "application", "domain", "infrastructure", "read_models", "workers")

FORBIDDEN = {
    ("api", "workers"),
    ("workers", "api"),
    ("application", "api"),
    ("application", "workers"),
    ("domain", "api"),
    ("domain", "workers"),
    ("domain", "application"),
    ("domain", "infrastructure"),
    ("domain", "read_models"),
    ("read_models", "workers"),
    ("infrastructure", "application"),
    ("infrastructure", "read_models"),
    ("application", "read_models"),
    ("read_models", "application"),
}


def is_layer(path: Path, root: Path) -> str | None:
    """Return the backend layer name if ``path`` is under ``backend/<layer>/``."""
    rel = path.relative_to(root)
    parts = rel.parts
    if len(parts) < 2 or parts[0] != "backend":
        return None
    layer = parts[1]
    return layer if layer in LAYER_ROOTS else None


def iter_python(root: Path):
    backend_root = root / "backend"
    if not backend_root.exists():
        return
    for path in backend_root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        yield path


def _target_layer(module_name: str) -> str | None:
    parts = module_name.split(".")
    if len(parts) >= 2 and parts[0] == "backend" and parts[1] in LAYER_ROOTS:
        return parts[1]
    return None


def check_file(path: Path, root: Path) -> tuple[list[tuple[str, str, int]], list[str]]:
    violations: list[tuple[str, str, int]] = []
    parse_errors: list[str] = []
    importer = is_layer(path, root)
    if importer is None:
        return violations, parse_errors

    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except SyntaxError as e:
        parse_errors.append(f"{path.relative_to(root)}: SyntaxError at line {e.lineno}: {e.msg}")
        return violations, parse_errors

    for node in ast.walk(tree):
        target = None
        if isinstance(node, ast.ImportFrom) and node.module:
            target = _target_layer(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                target = _target_layer(alias.name)
                if target:
                    break
        if target and (importer, target) in FORBIDDEN:
            violations.append((importer, target, node.lineno))
    return violations, parse_errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="Repository root (default: cwd).")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()

    all_violations: list[tuple[Path, str, str, int]] = []
    all_parse_errors: list[str] = []
    scanned = 0
    for path in iter_python(root):
        scanned += 1
        violations, parse_errors = check_file(path, root)
        for importer, target, lineno in violations:
            all_violations.append((path, importer, target, lineno))
        all_parse_errors.extend(parse_errors)

    all_violations.sort(key=lambda v: (str(v[0]), v[3]))

    if all_parse_errors:
        print(f"layer-deps-check: {len(all_parse_errors)} parse error(s):\n")
        for err in all_parse_errors:
            print(f"  {err}")
        print()

    if not all_violations:
        print(f"layer-deps-check: scanned {scanned} files; OK.")
        return 1 if all_parse_errors else 0

    print(f"layer-deps-check: scanned {scanned} files; {len(all_violations)} violation(s):\n")
    for path, importer, target, lineno in all_violations:
        rel = path.relative_to(root) if path.is_relative_to(root) else path
        print(f"  {rel}:{lineno}  backend.{importer} -> backend.{target}  (forbidden)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
