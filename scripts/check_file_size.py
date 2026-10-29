#!/usr/bin/env python3
"""File-size guard for InfoOS.

Enforces AGENTS.md「代码结构」 thresholds for backend Python and frontend React/Vue
code. Exits 0 if every file is within its threshold (or has a valid
`noqa: file-size` marker in the first 5 lines); 1 otherwise.

Usage:
    python scripts/check_file_size.py [--repo PATH] [--strict]

`--strict` treats files over the *recommended* (not must-split) threshold
as warnings turned into errors.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


# ---------- thresholds (AGENTS.md「代码结构」) ----------


@dataclass(frozen=True)
class Threshold:
    recommended: int
    must_split: int  # hard limit; files past this fail CI


# Frontend components: recommended ≤250, must split at 400
COMPONENT = Threshold(recommended=250, must_split=400)

# Controller / route: recommended ≤150, must split at 250
CONTROLLER = Threshold(recommended=150, must_split=250)

# Repository / DAO: recommended ≤300, must split at 500
REPOSITORY = Threshold(recommended=300, must_split=500)

# Service / business logic: recommended ≤400, must split at 600
SERVICE = Threshold(recommended=400, must_split=600)

# General / utility: recommended ≤400, must split at 600
GENERAL = Threshold(recommended=400, must_split=600)

# Entry-point files (CLI main, app entry) get +50% headroom
ENTRY_BONUS = 0.5


# ---------- classification ----------


# Path prefixes we never scan
SKIP_DIRS = {
    "node_modules",
    "dist",
    "build",
    ".worktrees",
    ".git",
    "__pycache__",
    "__tests__",
    ".next",
    "venv",
    ".venv",
    "vendor",
}

# Filename patterns we always skip
SKIP_FILE_PATTERNS = (
    "test_check_file_size.py",  # this script's own tests
)


def _is_skipped_dir(name: str) -> bool:
    return name in SKIP_DIRS


def _is_skipped_file(path: Path) -> bool:
    name = path.name
    if any(s in name for s in SKIP_FILE_PATTERNS):
        return True
    # Skip generated type defs
    if name.endswith(".d.ts"):
        return True
    # Skip test files: *.test.* / *.spec.* / __tests__/*
    if ".test." in name or ".spec." in name:
        return True
    return False


def classify(rel: Path) -> tuple[str, Threshold, bool]:
    """Return (kind, threshold, is_entry_point) for a repo-relative path.

    Classification precedence is important: more specific kinds win.
    """
    parts = rel.parts

    # Frontend components
    if len(parts) >= 3 and parts[0] == "frontend" and parts[1] == "src":
        ext = rel.suffix.lower()
        if ext in {".tsx", ".jsx", ".vue"}:
            return ("component", COMPONENT, False)
        # Non-component frontend files (e.g. api.ts, hooks) fall to general
        if ext in {".ts", ".js"}:
            return ("general", GENERAL, False)
        return ("general", GENERAL, False)

    # backend package
    if len(parts) >= 2 and parts[0] == "backend":
        layer = parts[1]
        # Entry points: backend/app/main.py or backend/workers/scheduler/cycle.py
        if layer == "app" and parts[-1] == "main.py":
            return ("service", SERVICE, True)
        if layer == "workers" and parts[-2:] == ("scheduler", "cycle.py"):
            return ("service", SERVICE, True)
        # Controllers: backend/api/routes/<file>.py
        if layer == "api" and len(parts) >= 3 and parts[2] == "routes":
            return ("controller", CONTROLLER, False)
        # Repositories: backend/infrastructure/storage/ + name matches
        if len(parts) >= 3 and parts[1:3] == ("infrastructure", "storage") and (
            "repositor" in rel.name or rel.name.endswith("ies.py")
        ):
            return ("repository", REPOSITORY, False)
        # Application use cases/workflows and workers
        if layer in {"application", "workers"} or rel.name.endswith("service.py"):
            return ("service", SERVICE, False)
        # Default for other backend files
        return ("service", SERVICE, False)

    return ("general", GENERAL, False)


# ---------- noqa detection ----------


NOQA_MARKER = "noqa: file-size"


def has_noqa_marker(path: Path) -> bool:
    """True iff the first 5 non-empty lines of `path` contain a noqa marker."""
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for i, line in enumerate(f):
                if i >= 5:
                    break
                if NOQA_MARKER in line:
                    return True
    except OSError:
        return False
    return False


# ---------- file discovery ----------


def iter_repo_files(repo: Path) -> Iterable[Path]:
    """Yield candidate files under backend/ and frontend/src/.

    `repo` is the repository root. We only scan paths that have meaningful
    thresholds defined in AGENTS.md.

    Skip-matching is done on the path *relative to `repo`*, so absolute
    paths (e.g. when the script is invoked from inside a worktree) don't
    accidentally match directory names that happen to be skipped.
    """
    targets = [repo / "backend", repo / "frontend" / "src"]
    for root in targets:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(repo)
            # Skip directories by name components (only on the relative path)
            if any(_is_skipped_dir(p) for p in rel.parts):
                continue
            if _is_skipped_file(path):
                continue
            yield path


# ---------- reporting ----------


@dataclass
class Violation:
    rel: Path
    kind: str
    lines: int
    threshold: int
    is_entry: bool
    is_above_recommended: bool  # used by --strict


def check_file(path: Path, repo: Path, strict: bool) -> tuple[Violation | None, str]:
    """Check one file. Returns (violation_or_None, kind_label)."""
    rel = path.relative_to(repo)
    kind, threshold, is_entry = classify(rel)

    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            line_count = sum(1 for _ in f)
    except OSError:
        return None, kind

    # Exemption
    if has_noqa_marker(path):
        return None, kind

    # Entry-point bonus
    limit_must = threshold.must_split
    if is_entry:
        limit_must = int(threshold.must_split * (1 + ENTRY_BONUS))
    limit_rec = threshold.recommended
    if is_entry:
        limit_rec = int(threshold.recommended * (1 + ENTRY_BONUS))

    if line_count > limit_must:
        return Violation(
            rel=rel,
            kind=kind,
            lines=line_count,
            threshold=limit_must,
            is_entry=is_entry,
            is_above_recommended=True,
        ), kind

    if strict and line_count > limit_rec:
        return Violation(
            rel=rel,
            kind=kind,
            lines=line_count,
            threshold=limit_must,
            is_entry=is_entry,
            is_above_recommended=True,
        ), kind

    return None, kind


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        default=".",
        help="Repository root (default: cwd).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail when a file exceeds the recommended (not must-split) limit.",
    )
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    if not repo.exists():
        print(f"error: repo not found: {repo}", file=sys.stderr)
        return 2

    violations: list[Violation] = []
    scanned = 0
    for path in iter_repo_files(repo):
        scanned += 1
        v, _ = check_file(path, repo, args.strict)
        if v is not None:
            violations.append(v)

    # Group by kind for readable output
    violations.sort(key=lambda v: (v.kind, -v.lines, str(v.rel)))

    print(f"file-size-check: scanned {scanned} files under {repo}")

    # Run CSS brace-balance sanity check (cheap, catches the bug class
    # we hit on 2026-06-05 where splitting styles.css dropped a `}` into
    # the wrong file and PostCSS failed at runtime in Vite).
    css_violations = check_css_brace_balance(repo)
    if css_violations:
        print(f"\nERROR: {len(css_violations)} CSS file(s) have unbalanced braces:\n")
        for rel, opens, closes in css_violations:
            print(f"  {rel}: opens={opens} closes={closes} (delta={opens - closes})")
        return 1

    if not violations:
        print("OK: no files exceed thresholds.")
        return 0

    print(f"\nERROR: {len(violations)} file(s) exceed size thresholds:\n")
    for v in violations:
        entry_tag = " (entry-point, +50%)" if v.is_entry else ""
        print(
            f"  [{v.kind}] {v.rel}: {v.lines} lines > must_split {v.threshold}{entry_tag}"
        )

    if args.strict:
        print(
            "\n(--strict: files over the *recommended* limit are also failures. "
            "Split or add `# noqa: file-size - reason: <text>` in the first 5 lines.)"
        )
    else:
        print(
            "\nFix: split the file, or add `# noqa: file-size - reason: <text>` "
            "in the first 5 lines (Python) or `// noqa: file-size - reason: <text>` "
            "(JS/TS) to exempt it from this check."
        )
    return 1


def check_css_brace_balance(repo: Path) -> list[tuple[Path, int, int]]:
    """Walk frontend/src/styles/*.css and return files with unbalanced
    braces. The split in PR #9 (refactor/file-size) once left a stray `}`
    at the top of blocks.css because each file was a verbatim chunk; this
    check makes sure no future split leaves dangling delimiters.
    """
    styles_dir = repo / "frontend" / "src" / "styles"
    if not styles_dir.is_dir():
        return []
    out: list[tuple[Path, int, int]] = []
    for css_path in sorted(styles_dir.glob("*.css")):
        text = css_path.read_text(encoding="utf-8", errors="ignore")
        opens = text.count("{")
        closes = text.count("}")
        if opens != closes:
            out.append((css_path.relative_to(repo), opens, closes))
    return out


if __name__ == "__main__":
    sys.exit(main())
