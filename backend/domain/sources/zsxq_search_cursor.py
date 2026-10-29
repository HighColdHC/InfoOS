"""Cursor policy for the ordering requested from the ZSXQ search adapter."""
from __future__ import annotations


def zsxq_search_result_order(command: list[str]) -> str:
    # The adapter preserves server ranking for comprehensive search. A scalar
    # first-result cursor would discard unseen results ranked below that row.
    sort = command[command.index("--sort") + 1] if "--sort" in command else "comprehensive"
    return {"newest": "newest_first", "earliest": "oldest_first"}.get(sort, "snapshot")
