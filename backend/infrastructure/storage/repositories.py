"""Public re-export hub for the storage repository classes.

The implementations live in per-aggregate modules under
``backend.infrastructure.storage.*``. This module provides one import location
for callers that need multiple repository classes:

    from backend.infrastructure.storage.repositories import SourceRepository

Keep new repository implementations in their focused modules and export them
here only when they are part of the public storage surface.
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
from backend.infrastructure.storage.card_assets import CardAssetRepository
from backend.infrastructure.storage.acquisition_checkpoints import AcquisitionCheckpointRepository
from backend.infrastructure.storage.capture_receipts import CaptureReceiptRepository
from backend.infrastructure.storage.auth_recovery import AuthRecoveryRepository
from backend.infrastructure.storage.auth_recheck import AuthRecheckRepository
from backend.infrastructure.storage.auth_recovery_reconciliation import (
    AuthRecoveryReconciliationRepository,
)
from backend.infrastructure.storage.audio_retention import AudioRetentionRepository
from backend.infrastructure.storage.card_content_blocks import CardContentBlockRepository
from backend.infrastructure.storage.cards import CardRepository
from backend.infrastructure.storage.fetch_rate_limits import FetchRateLimitRepository
from backend.infrastructure.storage.feishu_backfill import FeishuBackfillRepository
from backend.infrastructure.storage.raw_items import RawItemRepository
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
    "AcquisitionCheckpointRepository",
    "CaptureReceiptRepository",
    "AuthRecoveryRepository",
    "AuthRecheckRepository",
    "AuthRecoveryReconciliationRepository",
    "AudioRetentionRepository",
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
    "FetchRateLimitRepository",
    "FeishuBackfillRepository",
    "CardRepository",
    "CardContentBlockRepository",
    "CardAssetRepository",
    "TopicRepository",
    "RunRepository",
    "RawItemRepository",
    "RuntimeCircuitRepository",
    "RuntimeResourceLeaseRepository",
    "UserActionRepository",
]
