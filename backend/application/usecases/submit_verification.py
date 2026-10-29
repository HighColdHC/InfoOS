"""Save a confirmed user record, its block and durable finalize task atomically."""
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.application.tasks.finalization import enqueue_task
from backend.application.tasks.planner import versioned_task_id
from backend.application.tasks.types import Task, TaskType
from backend.domain.intelligence.verification import (
    VERIFICATION_SCHEMA_VERSION, VerificationError, format_verification, validate_verification, verification_request_hash,
)
from backend.domain.intelligence.opportunity import breakdown_snapshot
from backend.domain.timestamps import utc_now_micro_z
from backend.infrastructure.storage.base import utc_now
from backend.infrastructure.storage.opportunity_verifications import OpportunityVerificationRepository
from backend.infrastructure.storage.opportunity_breakdowns import OpportunityBreakdownRepository
from backend.infrastructure.storage.repositories import CardContentBlockRepository, CardRepository


def submit_verification(base_dir: Path, payload: dict[str, Any], *, token_id: str):
    fields = validate_verification(payload)
    card_id = fields["card_id"]
    request_hash = verification_request_hash(fields)
    record = {**fields, "verification_id": "verification_" + uuid4().hex, "token_id": token_id,
              "schema_version": VERIFICATION_SCHEMA_VERSION, "created_at": utc_now_micro_z(),
              "request_content_hash": request_hash, "breakdown_snapshot": None}
    cards, blocks = CardRepository(base_dir), CardContentBlockRepository(base_dir)
    with cards.connect() as conn:
        conn.execute("begin immediate")
        previous = OpportunityVerificationRepository.find_submission(conn, token_id, fields["client_verification_id"])
        if previous is not None:
            if previous["request_content_hash"] != request_hash:
                raise VerificationError("verification_conflict", "同一 client_verification_id 已提交不同内容，请为新核实使用新 ID")
            return _result(previous, replayed=True)
        card = cards.get_card(card_id, connection=conn)
        if card is None:
            raise VerificationError("card_not_found", "卡片不存在", "card_id")
        if fields["supersedes"] is not None and not OpportunityVerificationRepository.belongs_to_card(
                conn, fields["supersedes"], card_id):
            raise VerificationError("invalid_supersedes", "被更正记录必须存在且属于同一卡片", "supersedes")
        if fields["breakdown_id"] is not None:
            selected = OpportunityBreakdownRepository.get(conn, fields["breakdown_id"])
            if selected is None or selected["card_id"] != card_id:
                raise VerificationError("invalid_breakdown", "关联拆解必须存在且属于同一卡片", "breakdown_id")
            latest = OpportunityBreakdownRepository(base_dir).latest_for_card(card_id, connection=conn)
            snapshot = breakdown_snapshot(selected)
            if (latest["breakdown_id"] != selected["breakdown_id"]
                    or snapshot["breakdown_content_hash"] != fields["expected_breakdown_hash"]):
                raise VerificationError("breakdown_changed", "拆解已变化，请重新读取并与用户确认")
            record["breakdown_snapshot"] = snapshot
        OpportunityVerificationRepository.append(conn, record)
        old = blocks.list_for_card(card_id, connection=conn)
        blocks.upsert_block({
            "block_id": record["verification_id"] + "_block", "card_id": card_id,
            "position": max((b["position"] for b in old), default=0) + 1,
            "layer": "intelligence", "block_kind": "opportunity_verification", "status": "enriched",
            "body": format_verification(record), "provenance": "user_verified.via_agent",
            "raw_payload": {"purpose": "user_verified.via_agent", "schema_version": VERIFICATION_SCHEMA_VERSION,
                            "verification_id": record["verification_id"], "recorder": fields["recorder"]},
        }, connection=conn)
        version = f"verification:{record['verification_id']}"
        task_id = _result(record, replayed=False)["finalize_task_id"]
        enqueue_task(conn, Task(task_id=task_id, task_type=TaskType.CARD_FINALIZE,
            target_kind="card", target_id=card_id, input_version=version,
            artifacts_summary={"card_id": card_id, "verification_finalize": True}), now=utc_now())
    return _result(record, replayed=False)


def _result(record, *, replayed):
    version = f"verification:{record['verification_id']}"
    task_id = versioned_task_id(TaskType.CARD_FINALIZE, record["card_id"], sha256(version.encode()).hexdigest())
    return {"verification_id": record["verification_id"], "card_id": record["card_id"],
            "finalize_task_id": task_id, "replayed": replayed}
