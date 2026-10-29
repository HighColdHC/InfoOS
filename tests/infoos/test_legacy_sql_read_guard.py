"""Keep retired SQL tables inside explicit historical compatibility modules."""
from __future__ import annotations

import ast
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
LEGACY_TABLES = (
    "semantic_annotations", "value_scores", "decision_records", "output_artifacts",
    "user_actions", "user_comments", "reading_queue", "materials",
    "topics", "topic_cards", "topic_writing_context_exports", "events", "event_cards",
)
# Each entry is documented in docs/engineering/legacy-sql-read-allowlist.md.
LEGACY_SQL_ALLOWLIST = {
    "application/tasks/chain_outputs.py",
    "infrastructure/storage/migrations/v0002_legacy_backfills.py",
    "infrastructure/storage/topics.py",
    "infrastructure/storage/user_actions.py",
}
PATTERN = re.compile(r"\b(?:from|join)\s+(?:" + "|".join(LEGACY_TABLES) + r")\b", re.I)


def test_current_modules_do_not_query_legacy_tables() -> None:
    offenders = set()
    for path in BACKEND.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        # Inspect SQL literals (including f-string fragments), not comments.
        if any(
            PATTERN.search(node.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ):
            offenders.add(path.relative_to(BACKEND).as_posix())
    assert offenders <= LEGACY_SQL_ALLOWLIST, sorted(offenders - LEGACY_SQL_ALLOWLIST)
