"""Keep legacy count boundaries when status scans use larger physical batches."""

from __future__ import annotations

from backend.infrastructure.storage.source_run_list_queries_types import CatalogRow, CatalogSummary


def status_batch_prefix(
    rows: list[CatalogRow],
    *,
    candidate_rank: dict[str, int],
    candidate_count: int,
    batch_size: int,
    status: str,
    matches_needed: int,
) -> tuple[list[CatalogRow], CatalogSummary, int]:
    """Stop counts at the same whole logical batch as the fixed-size scanner."""
    consumed = candidate_count
    for row in rows:
        if row["product_status"] == status:
            matches_needed -= 1
            if matches_needed == 0:
                rank = candidate_rank[row["stable_id"]]
                consumed = min(candidate_count, (rank // batch_size + 1) * batch_size)
                break
    kept = [row for row in rows if candidate_rank[row["stable_id"]] < consumed]
    summary: CatalogSummary = {
        "last_24h": sum(bool(row["_scan_last_24h"]) for row in kept),
        "completed": sum(row["product_status"] == "completed" for row in kept),
        "attention": sum(row["product_status"] == "attention" for row in kept),
        "active": sum(row["product_status"] in ("running", "pending") for row in kept),
    }
    return kept, summary, consumed
