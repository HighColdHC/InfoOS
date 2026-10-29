from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.application.tasks.types import Task, TaskStatus
from backend.infrastructure.storage.paths import resolve_data_dir
from backend.application.tasks.media_outputs import media_content_version, media_output_present


def handle_video_extract_audio(task: Task, base_dir: Path) -> dict[str, Any]:
    from backend.application.enrichment.media.video_transcription import extract_audio
    from backend.application.enrichment.transcripts.native import write_native_transcript_block_if_available
    from backend.infrastructure.storage.repositories import CardAssetRepository, CardContentBlockRepository

    asset_repo = CardAssetRepository(base_dir)
    asset = asset_repo.get_asset(task.target_id)
    if not asset:
        return {"status": TaskStatus.SKIPPED.value, "artifacts": _missing_asset_artifacts(task, "asset not found")}
    if media_output_present(asset, CardContentBlockRepository(base_dir).list_for_card(asset["card_id"])):
        return {"status": "skipped", "artifacts": _asset_task_artifacts(asset, success=True) | {"transcript_satisfied": True, "reason": "output_present"}}
    native = write_native_transcript_block_if_available(base_dir, asset)
    if native is not None:
        update_asset_transcription(asset_repo, asset, native, asset_status="understood")
        return {
            "status": TaskStatus.SKIPPED.value,
            "artifacts": {
                **transcript_artifacts(asset, native),
                "transcript_satisfied": True,
            },
        }
    local_path = resolve_asset_local_path(base_dir, str(asset.get("local_path") or ""))
    result = extract_audio(str(local_path), output_dir=str(resolve_data_dir(base_dir) / "audio"))
    if result.get("error"):
        no_audio_stream = is_no_audio_stream_error(result.get("error"))
        transcription_status = "not_applicable" if no_audio_stream else "failed"
        record_optional_derivation_failure(
            asset_repo,
            asset,
            result.get("error"),
            local_path=local_path,
            status_key="transcription_status",
            error_key="transcription_error",
            derived_status=transcription_status,
            extra_raw_payload={
                **({"derived_from_media_version": media_content_version(asset)} if no_audio_stream else {}),
                "understanding_status": "skipped" if no_audio_stream else transcription_status,
                "understanding_error": result.get("error"),
                **(
                    {"transcription_reason_code": "no_audio_stream"}
                    if no_audio_stream
                    else {}
                ),
            },
        )
        if is_nonfatal_video_audio_error(result.get("error")):
            return {
                "status": TaskStatus.SKIPPED.value,
                "artifacts": {
                    **_asset_task_artifacts(asset, success=False, reason=result.get("error")),
                    # A video without an audio stream is the only supported
                    # terminal state where a transcript is genuinely not
                    # applicable.  Keep it explicit so finalization does not
                    # confuse it with an ASR/runtime failure.
                    "transcript_not_applicable": no_audio_stream,
                },
            }
        raise RuntimeError(str(result.get("error")))
    return {
        "status": TaskStatus.SUCCESS.value,
        "artifacts": {
            **_asset_task_artifacts(asset, success=True),
            "audio_path": result.get("audio_path"),
            "duration_seconds": result.get("duration_seconds", 0),
            "format": result.get("format"),
        },
    }


def handle_video_transcribe(task: Task, base_dir: Path) -> dict[str, Any]:
    from backend.application.enrichment.transcripts.service import extract_video_transcript
    from backend.infrastructure.storage.repositories import CardAssetRepository, CardContentBlockRepository

    asset_repo = CardAssetRepository(base_dir)
    asset = asset_repo.get_asset(task.target_id)
    if not asset:
        return {"status": TaskStatus.SKIPPED.value, "artifacts": _missing_asset_artifacts(task, "asset not found")}
    if media_output_present(asset, CardContentBlockRepository(base_dir).list_for_card(asset["card_id"])):
        return {"status": "skipped", "artifacts": _asset_task_artifacts(asset, success=True) | {"transcript_satisfied": True, "reason": "output_present"}}
    audio_path = task.artifacts_summary.get("audio_path")
    result = extract_video_transcript(
        base_dir=base_dir,
        asset=asset,
        audio_path=str(audio_path) if audio_path else None,
        duration_seconds=float_or_none(task.artifacts_summary.get("duration_seconds")),
        quality_mode=str(task.artifacts_summary.get("quality_mode") or "") or None,
    )
    transcript_text = str(result.get("text") or "").strip()
    status = str(result.get("status") or TaskStatus.SUCCESS.value)
    no_speech_detected = is_no_speech_detected_result(result)
    if status == "success" and transcript_text:
        block_repo = CardContentBlockRepository(base_dir)
        block_repo.upsert_block(
            {
                "block_id": f"block_{asset['card_id']}_video_transcript_{asset['asset_id']}",
                "card_id": asset["card_id"],
                "position": next_block_position(block_repo, asset["card_id"]),
                "layer": "derived",
                "block_kind": "video_transcript",
                "status": "enriched",
                "body": transcript_text,
                "segments": result.get("segments") if isinstance(result.get("segments"), list) else [],
                "source_url": f"asset:{asset['asset_id']}",
                "provenance": "video_transcribe",
                "raw_payload": transcript_block_payload(asset, result),
            }
        )
    elif no_speech_detected:
        # A no-speech VAD result is a terminal, not-applicable media outcome.
        # Remove any stale ASR block for this asset so it cannot make a later
        # CARD_FINALIZE look as though this attempt produced a transcript.
        _remove_transcript_blocks_for_asset(
            CardContentBlockRepository(base_dir),
            asset,
        )
    task_status = transcript_task_status(status)
    local_path = resolve_asset_local_path(base_dir, str(asset.get("local_path") or ""))
    asset_status = (
        "understood"
        if task_status in {TaskStatus.SUCCESS, TaskStatus.SKIPPED}
        else "downloaded" if local_path.exists() else "failed"
    )
    update_asset_transcription(asset_repo, asset, result, asset_status=asset_status)
    artifacts = transcript_artifacts(asset, result)
    if no_speech_detected:
        artifacts["transcript_not_applicable"] = True
    return {
        "status": task_status.value,
        "artifacts": artifacts,
        "error": result.get("error") if task_status in {TaskStatus.BLOCKED, TaskStatus.FAILED} else None,
    }


def resolve_asset_local_path(base_dir: Path, local_path: str) -> Path:
    path = Path(local_path)
    if path.is_absolute():
        return path
    return resolve_data_dir(base_dir) / "assets" / path


def record_optional_derivation_failure(
    asset_repo: Any,
    asset: dict[str, Any],
    error: Any,
    *,
    local_path: Path,
    status_key: str,
    error_key: str,
    derived_status: str = "failed",
    extra_raw_payload: dict[str, Any] | None = None,
) -> None:
    updated = dict(asset)
    updated["status"] = "downloaded" if local_path.is_file() else "failed"
    updated["raw_payload"] = {
        **(asset.get("raw_payload") or {}),
        **(extra_raw_payload or {}),
        status_key: derived_status,
        error_key: str(error),
    }
    asset_repo.upsert_asset(updated)


def is_nonfatal_video_audio_error(error: Any) -> bool:
    text = str(error or "")
    return text.startswith("ffmpeg not installed") or is_no_audio_stream_error(text)


def is_no_audio_stream_error(error: Any) -> bool:
    return "no audio stream" in str(error or "").lower()


def is_no_speech_detected_result(result: dict[str, Any]) -> bool:
    return (
        str(result.get("status") or "") == TaskStatus.SKIPPED.value
        and str(result.get("error") or "") == "no_speech_detected"
    )


def transcript_task_status(status: str) -> TaskStatus:
    if status == "blocked":
        return TaskStatus.BLOCKED
    if status == "failed":
        return TaskStatus.FAILED
    if status == "skipped":
        return TaskStatus.SKIPPED
    return TaskStatus.SUCCESS


def transcript_block_payload(asset: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    return {
        "asset_id": asset["asset_id"],
        "native_transcript_used": bool(result.get("native_transcript_used")),
        "asr_provider": result.get("provider"),
        "model": result.get("model"),
        "language": result.get("language"),
        "language_confidence": result.get("language_confidence"),
        "duration_seconds": result.get("duration_seconds"),
        "duration_after_vad": result.get("duration_after_vad"),
        "rtf": result.get("rtf"),
        "fallback_chain": result.get("fallback_chain") if isinstance(result.get("fallback_chain"), list) else [],
        "quality_flags": result.get("quality_flags") if isinstance(result.get("quality_flags"), list) else [],
    }


def transcript_artifacts(asset: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    transcript_text = str(result.get("text") or "").strip()
    quality_flags = result.get("quality_flags") if isinstance(result.get("quality_flags"), list) else []
    artifacts = {
        "card_id": asset["card_id"],
        "asset_id": asset["asset_id"],
        "asset_kind": asset["asset_kind"],
        "total": 1,
        "success": int(str(result.get("status") or "success") in {"success", "skipped"}),
        "failed": int(str(result.get("status") or "success") in {"failed", "blocked"}),
        "failure_class": (
            f"video_transcription_{str(result.get('status'))}"
            if str(result.get("status") or "") in {"failed", "blocked"}
            else None
        ),
        "reason": result.get("error"),
        "text_chars": len(transcript_text),
        "native_transcript_used": bool(result.get("native_transcript_used")),
        "detected_language": result.get("language"),
        "language_confidence": result.get("language_confidence"),
        "route": result.get("route") or result.get("model"),
        "provider": result.get("provider"),
        "model": result.get("model"),
        "rtf": result.get("rtf"),
        "audio_duration_seconds": result.get("duration_seconds"),
        "duration_after_vad": result.get("duration_after_vad"),
        "fallback_chain": result.get("fallback_chain") if isinstance(result.get("fallback_chain"), list) else [],
        "blocked_reason": result.get("error") if str(result.get("status") or "") == "blocked" else None,
        "quality_flags": quality_flags or None,
    }
    if transcript_text:
        artifacts["transcript_text"] = transcript_text
    if is_no_speech_detected_result(result):
        artifacts["transcript_not_applicable"] = True
    return {key: value for key, value in artifacts.items() if value is not None}


def _asset_task_artifacts(
    asset: dict[str, Any],
    *,
    success: bool,
    reason: Any = None,
) -> dict[str, Any]:
    return {
        "card_id": asset["card_id"],
        "asset_id": asset["asset_id"],
        "asset_kind": asset["asset_kind"],
        "total": 1,
        "success": int(success),
        "failed": int(not success),
        "failure_class": "media_intelligence_failed" if reason else None,
        "reason": reason,
    }


def _missing_asset_artifacts(task: Task, reason: str) -> dict[str, Any]:
    return {
        "card_id": None,
        "asset_id": task.target_id,
        "asset_kind": "unknown",
        "total": 0,
        "success": 0,
        "failed": 0,
        "failure_class": "asset_not_found",
        "reason": reason,
    }


def update_asset_transcription(
    asset_repo: Any,
    asset: dict[str, Any],
    result: dict[str, Any],
    *,
    asset_status: str,
) -> None:
    status = str(result.get("status") or "success")
    error = result.get("error")
    no_speech_detected = is_no_speech_detected_result(result)
    updated = dict(asset)
    updated["status"] = asset_status
    if status == "success":
        derived_status = "enriched"
        transcription_status = "enriched"
    elif no_speech_detected:
        derived_status = "skipped"
        transcription_status = "not_applicable"
    else:
        derived_status = str(error or status)
        transcription_status = derived_status
    raw_payload = {
        **(asset.get("raw_payload") or {}),
        "understanding_status": derived_status,
        "transcription_status": transcription_status,
        "transcription_error": error,
        "asr_provider": result.get("provider"),
        "asr_model": result.get("model"),
        "asr_route": result.get("route") or result.get("model"),
        "asr_fallback_chain": result.get("fallback_chain") if isinstance(result.get("fallback_chain"), list) else [],
        "asr_language": result.get("language"),
        "asr_language_confidence": result.get("language_confidence"),
        "asr_rtf": result.get("rtf"),
    }
    if status == "success" or no_speech_detected:
        raw_payload["derived_from_media_version"] = media_content_version(asset)
    if no_speech_detected:
        raw_payload["transcription_reason_code"] = "no_speech_detected"
        raw_payload["transcript_not_applicable"] = True
    else:
        raw_payload.pop("transcription_reason_code", None)
        raw_payload.pop("transcript_not_applicable", None)
    if status == "success":
        raw_payload.pop("transcription_error", None)
    updated["raw_payload"] = raw_payload
    asset_repo.upsert_asset(updated)


def _remove_transcript_blocks_for_asset(block_repo: Any, asset: dict[str, Any]) -> None:
    asset_id = asset["asset_id"]
    blocks = block_repo.list_for_card(asset["card_id"])
    block_ids = [
        block["block_id"]
        for block in blocks
        if block["block_kind"] == "video_transcript"
        and (
            (
                isinstance(block["raw_payload"], dict)
                and str(block["raw_payload"].get("asset_id") or "") == asset_id
            )
            or block["source_url"] == f"asset:{asset_id}"
        )
    ]
    block_repo.delete_blocks(block_ids)


def float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def next_block_position(block_repo: Any, card_id: str, *, connection=None) -> int:
    blocks = block_repo.list_for_card(card_id, connection=connection)
    return max((block["position"] for block in blocks), default=-1) + 1
