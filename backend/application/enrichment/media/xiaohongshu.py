from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from backend.application.enrichment.media.artifacts import (
    create_media_blocks,
    list_media_files,
    upsert_media_gap_block,
)
from backend.infrastructure.opencli.commands import pin_adapter_lifecycle
from backend.infrastructure.opencli.runtime import resolve_opencli_executable
from backend.infrastructure.security.redaction import summarize_subprocess_output
from backend.infrastructure.opencli.tab_lease import opencli_tab_lease
from backend.infrastructure.storage.paths import resolve_data_dir


def download_xiaohongshu_card_media(
    card_id: str,
    note_url: str,
    base_dir: Path,
) -> list[dict[str, Any]]:
    from backend.infrastructure.storage.repositories import CardAssetRepository, CardContentBlockRepository

    block_repo = CardContentBlockRepository(base_dir)
    if not note_url:
        upsert_media_gap_block(
            block_repo,
            card_id=card_id,
            source_url="",
            reason="xiaohongshu_media_download_missing_note_url",
        )
        return []
    assets_root = resolve_data_dir(base_dir) / "assets"
    assets_dir = assets_root / card_id / "xiaohongshu"
    assets_dir.mkdir(parents=True, exist_ok=True)
    asset_repo = CardAssetRepository(base_dir)
    existing = list_media_files(assets_dir, relative_base=assets_root)
    if existing:
        return create_media_blocks(
            card_id,
            note_url,
            existing,
            asset_repo,
            block_repo,
            sub_kind="xiaohongshu",
            provenance_prefix="Xiaohongshu",
        )
    try:
        with opencli_tab_lease("xiaohongshu", close_tabs=False):
            result = subprocess.run(
                [resolve_opencli_executable("opencli"), *pin_adapter_lifecycle([
                    "xiaohongshu",
                    "download",
                    note_url,
                    "--output",
                    str(assets_dir),
                    "--format",
                    "json",
                    "--window",
                    "background",
                ], site_session="persistent", keep_tab=False)],
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
    except FileNotFoundError:
        upsert_media_gap_block(
            block_repo,
            card_id=card_id,
            source_url=note_url,
            reason="xiaohongshu_media_download_opencli_missing",
        )
        return []
    except subprocess.TimeoutExpired:
        upsert_media_gap_block(
            block_repo,
            card_id=card_id,
            source_url=note_url,
            reason="xiaohongshu_media_download_timeout",
        )
        return []
    if result.returncode != 0:
        upsert_media_gap_block(
            block_repo,
            card_id=card_id,
            source_url=note_url,
            reason="xiaohongshu_media_download_failed",
            details={
                "exit_code": result.returncode,
                "stderr_summary": summarize_subprocess_output(result.stdout, result.stderr),
            },
        )
        return []
    downloaded = list_media_files(assets_dir, relative_base=assets_root)
    blocks = create_media_blocks(
        card_id,
        note_url,
        downloaded,
        asset_repo,
        block_repo,
        sub_kind="xiaohongshu",
        provenance_prefix="Xiaohongshu",
    )
    if not blocks:
        upsert_media_gap_block(
            block_repo,
            card_id=card_id,
            source_url=note_url,
            reason="xiaohongshu_media_download_no_media_files",
        )
    return blocks
