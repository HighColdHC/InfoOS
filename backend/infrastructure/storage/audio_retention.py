"""Durable task evidence used by intermediate audio retention cleanup."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.infrastructure.storage.base import (
    InfoOSRepository,
    dumps,
    loads,
)
from backend.infrastructure.storage.task_events import TaskEventRepository


class AudioRetentionRepository(InfoOSRepository):
    """Query and mark WAV files whose successful transcription is durable."""

    def list_candidates(
        self,
        *,
        completed_before: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        with self.connect_existing() as conn:
            rows = conn.execute(
                """
                select
                  min(success.task_id) as task_id,
                  json_extract(
                    success.artifacts_summary,
                    '$.audio_path'
                  ) as audio_path,
                  max(coalesce(success.finished_at, success.updated_at))
                    as completed_at
                from tasks success
                where success.task_type = 'video_transcribe'
                  and success.status = 'success'
                  and coalesce(success.finished_at, success.updated_at) <= ?
                  and json_extract(
                    success.artifacts_summary,
                    '$.audio_path'
                  ) is not null
                  and json_extract(
                    success.artifacts_summary,
                    '$.audio_retention_status'
                  ) is null
                  and not exists (
                    select 1
                    from tasks referenced
                    where referenced.task_type = 'video_transcribe'
                      and referenced.status in (
                        'pending', 'running', 'failed', 'blocked'
                      )
                      and json_extract(
                        referenced.artifacts_summary,
                        '$.audio_path'
                      ) = json_extract(
                        success.artifacts_summary,
                        '$.audio_path'
                      )
                  )
                group by json_extract(
                  success.artifacts_summary,
                  '$.audio_path'
                )
                order by completed_at, task_id
                limit ?
                """,
                (completed_before, max(1, min(int(limit), 5000))),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_disposition(
        self,
        *,
        audio_path: str,
        representative_task_id: str,
        status: str,
        processed_at: str,
        bytes_reclaimed: int,
        retention_days: int,
    ) -> None:
        """Mark every successful producer and append one compact audit event."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                select task_id, artifacts_summary
                from tasks
                where task_type = 'video_transcribe'
                  and status = 'success'
                  and json_extract(
                    artifacts_summary,
                    '$.audio_path'
                  ) = ?
                """,
                (audio_path,),
            ).fetchall()
            for row in rows:
                artifacts = loads(row["artifacts_summary"], {})
                artifacts.update(
                    {
                        "audio_retention_status": status,
                        "audio_retention_processed_at": processed_at,
                        "audio_retention_bytes_reclaimed": bytes_reclaimed,
                        "audio_retention_days": retention_days,
                    }
                )
                conn.execute(
                    """
                    update tasks
                    set artifacts_summary = ?
                    where task_id = ? and status = 'success'
                    """,
                    (dumps(artifacts), row["task_id"]),
                )

            representative = next(
                (
                    loads(row["artifacts_summary"], {})
                    for row in rows
                    if row["task_id"] == representative_task_id
                ),
                {},
            )
            TaskEventRepository.append_to_connection(
                conn,
                event_type=f"audio_retention_{status}",
                task_id=representative_task_id,
                chain_id=str(representative.get("chain_id") or "") or None,
                status="success",
                payload={
                    "audio_file": Path(audio_path).name,
                    "bytes_reclaimed": bytes_reclaimed,
                    "retention_days": retention_days,
                },
                occurred_at=processed_at,
            )
