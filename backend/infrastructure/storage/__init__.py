"""Storage helpers for 信枢 MVP-0.

This package is the canonical storage layer for the infoos pipeline.
After the FastAPI Web Split Recovery (Phase 0), this __init__.py
aggregates the public storage symbols so that
``from backend.infrastructure.storage import X`` and
``from backend.infrastructure.storage.repositories import X`` resolve to
the same objects.

Note: ``bootstrap.ensure_workbench_seeded`` is intentionally NOT
re-exported here — importing it eagerly causes a circular import
through backend.domain.sources.configuration. Callers should import it
explicitly from ``backend.infrastructure.storage.bootstrap``.
"""

from backend.infrastructure.storage.base import (
    InfoOSRepository,
    card_quality_payload,
    clean_export_timestamp,
    dumps,
    loads,
    sort_timestamp,
    utc_now,
)
from backend.infrastructure.storage.auth_recovery_reconciliation import (
    AuthRecoveryReconciliationRepository,
)
from backend.infrastructure.storage.card_assets import CardAssetRepository
from backend.infrastructure.storage.card_content_blocks import CardContentBlockRepository
from backend.infrastructure.storage.cards import CardRepository
from backend.infrastructure.storage.raw_items import RawItemRepository
from backend.infrastructure.storage.raw_store import RawItemStore
from backend.infrastructure.storage.runtime_circuits import RuntimeCircuitRepository
from backend.infrastructure.storage.runtime_resource_leases import RuntimeResourceLeaseRepository
from backend.infrastructure.storage.runs import RunRepository
from backend.infrastructure.storage.source_cursors import SourceCursorRepository
from backend.infrastructure.storage.source_calibrations import SourceCalibrationRepository
from backend.infrastructure.storage.task_events import TaskEventRepository
from backend.infrastructure.storage.sources import SourceRepository
from backend.infrastructure.storage.topics import TopicRepository
from backend.infrastructure.storage.user_actions import UserActionRepository

__all__ = [
    "InfoOSRepository",
    "AuthRecoveryReconciliationRepository",
    "utc_now",
    "dumps",
    "loads",
    "card_quality_payload",
    "sort_timestamp",
    "clean_export_timestamp",
    "SourceRepository",
    "SourceCalibrationRepository",
    "SourceCursorRepository",
    "TaskEventRepository",
    "CardRepository",
    "CardContentBlockRepository",
    "CardAssetRepository",
    "TopicRepository",
    "RunRepository",
    "RawItemRepository",
    "RawItemStore",
    "RuntimeCircuitRepository",
    "RuntimeResourceLeaseRepository",
    "UserActionRepository",
]
