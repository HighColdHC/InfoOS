"""Catalog SQL row and paging shapes."""
from typing import NotRequired, TypedDict

from backend.infrastructure.storage.source_run_trace_queries_types import SourceRunSummaryRow


class CatalogSummary(TypedDict):
    last_24h: int
    completed: int
    attention: int
    active: int


class CatalogRow(SourceRunSummaryRow):
    product_status: str
    execution_bucket: str
    projected_card_total: int
    projected_card_complete: int
    projected_card_partial: int
    projected_card_failed: int
    _scan_last_24h: NotRequired[int | None]


class CatalogPage(TypedDict):
    rows: list[CatalogRow]
    total: int
    summary: CatalogSummary
    total_exact: bool
    counts_exact: bool
    has_more: bool


class CatalogBatch(TypedDict):
    rows: list[CatalogRow]
    total: int
    summary: CatalogSummary
    candidate_count: int
    next_cursor: tuple[int, float, str] | None
