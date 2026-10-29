"""Cursor and budget helpers for bounded pipeline reconciliation scans."""

from __future__ import annotations

import json


def load_raw_cursor(value: str | None) -> tuple[str, str]:
    if not value:
        return "", ""
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return "", ""
    if not isinstance(decoded, list) or len(decoded) != 2:
        return "", ""
    return str(decoded[0] or ""), str(decoded[1] or "")


def fair_scan_budgets(limit: int) -> tuple[int, int, int]:
    """Reserve bounded work for raw, card, and batch-link repairs."""
    total = max(0, int(limit))
    shared, remainder = divmod(total, 3)
    return (
        shared + (1 if remainder >= 1 else 0),
        shared + (1 if remainder >= 2 else 0),
        shared,
    )
