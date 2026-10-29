"""Shared test factories for building source payloads and DB rows.

These were introduced alongside the A1 (YAML → SQLite) soft switch so
test fixtures can build a single canonical source record and tweak only
the fields that matter, instead of duplicating the full payload in every
test that needs a source.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

# Ensure project root is importable for tests that need it
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from backend.domain.sources.configuration import validate_source_payload  # noqa: E402


def cards_for_source(base_dir: Path, source_id: str) -> list[dict[str, Any]]:
    """Read only one fixture source, then use the normal single-card decoder."""
    from backend.infrastructure.storage.repositories import CardRepository

    repo = CardRepository(base_dir)
    with repo.connect_existing() as conn:
        rows = conn.execute(
            "select card_id from cards where source_id = ? order by card_id",
            (source_id,),
        ).fetchall()
        return [repo.get_card(row["card_id"], connection=conn) for row in rows]


def card_count(base_dir: Path) -> int:
    """Count persisted fixture cards without hydrating their contents."""
    from backend.infrastructure.storage.repositories import CardRepository

    with CardRepository(base_dir).connect_existing() as conn:
        return conn.execute("select count(*) from cards").fetchone()[0]


def upsert_current_insight(base_dir: Path, card_id: str, payload: dict[str, Any]) -> None:
    """Persist historical Summary evidence used by legacy read models."""
    from backend.application.tasks.repository import TaskRepository
    from backend.application.tasks.types import Task, TaskStatus, TaskType
    from tests.infoos._legacy_history import seed_legacy_insight

    task = Task(
        task_id=f"historical-summary-{card_id}",
        task_type=TaskType.SUMMARY,
        target_kind="card",
        target_id=card_id,
        status=TaskStatus.SUCCESS,
        input_version="historical-summary-v1",
        result_version="historical-summary-v1",
    )
    TaskRepository.for_base_dir(base_dir).enqueue(task)
    seed_legacy_insight(
        base_dir, card_id,
        {
            **payload,
            "model": payload.get("model") or "fixture-model",
            "prompt_version": payload.get("prompt_version") or "fixture-prompt",
            "input_version": task.input_version,
        },
    )


def normalize_opencli_command(command: list[str]) -> list[str]:
    """Normalize resolved OpenCLI executable paths for command assertions."""
    if command and os.path.basename(str(command[0])) == "opencli":
        return ["opencli", *command[1:]]
    return list(command)


def opencli_command_startswith(command: list[str], *parts: str) -> bool:
    """Match adapter commands while allowing the InfoOS global profile prefix."""
    normalized = normalize_opencli_command(command)
    expected = list(parts)
    if (
        len(normalized) >= 3
        and normalized[1:3] == ["--profile", "infoos-fetch"]
        and expected[1:2] != ["--profile"]
    ):
        normalized = [normalized[0], *normalized[3:]]
    return normalized[: len(expected)] == expected


def make_source_payload(**overrides: Any) -> dict[str, Any]:
    """Build a valid source payload suitable for ``SourceRepository.upsert_source``.

    The defaults describe a minimal RSS feed. Override any field by
    passing it as a keyword argument. The result is run through
    ``validate_source_payload`` so callers can rely on the validated
    ``enabled`` and ``connector_status`` fields being present.
    """
    base: dict[str, Any] = {
        "source_id": "rss_test",
        "platform": "rss",
        "source_type": "feed",
        "identifier": "https://example.com/feed.xml",
        "display_name": "Test Feed",
        "default_domain_id": None,
        "cadence_hours": 24.0,
        "notes": "Test source",
        "enabled": True,
        "connector_id": "rss_test",
        "raw_metadata": {},
    }
    base.update(overrides)
    return validate_source_payload(base)


def make_supported_source_payload(**overrides: Any) -> dict[str, Any]:
    payload = make_source_payload(**overrides)
    payload["connector_status"] = "supported"
    return payload


def make_x_source_payload(**overrides: Any) -> dict[str, Any]:
    """Convenience for an X (Twitter) creator source."""
    base: dict[str, Any] = {
        "source_id": "x_test_creator",
        "platform": "x",
        "source_type": "creator",
        "identifier": "testcreator",
        "display_name": "@testcreator",
        "cadence_hours": 1.0,
        "enabled": True,
        "connector_id": "opencli_x_test_creator",
        "raw_metadata": {"source_lane": "primary", "topics": ["test"]},
    }
    base.update(overrides)
    return validate_source_payload(base)


def feed_xml() -> str:
    """Return a minimal RSS XML string with one item."""
    return """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel><title>Example</title>
      <item>
        <guid>item-1</guid>
        <title>First Post</title>
        <link>https://example.com/posts/1</link>
        <author>alice@example.com (Alice)</author>
        <pubDate>Fri, 18 Apr 2026 08:00:00 GMT</pubDate>
        <description><![CDATA[<p>Hello <b>world</b>.</p>]]></description>
      </item>
    </channel></rss>
    """

class DummyResponse:
    """Minimal stand-in for ``requests.Response`` used in CLI ingest tests."""

    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self):
        return None

def setup_card_and_asset(
    tmp_path: Path,
    card_id: str,
    asset_kind: str,
    local_path: str,
    asset_id: str | None = None,
    status: str = "downloaded",
    source_url: str = "https://x.com/example/1",
) -> None:
    """Create a card and asset record in the test database."""
    from backend.infrastructure.storage.repositories import CardAssetRepository, CardRepository

    CardRepository(tmp_path).upsert_card({
        "card_id": card_id,
        "source_id": None,
        "source_platform": "x",
        "source_type": "social_post",
        "source_url": source_url,
        "source_author": "test_author",
        "title": "Test Card",
        "content": "Test content",
        "summary": "",
        "primary_domain_id": None,
        "secondary_domain_ids": [],
        "published_at": "2026-05-10T01:00:00Z",
        "captured_at": "2026-05-10T01:00:00Z",
        "processed_at": "2026-05-10T01:00:00Z",
        "raw_payload": {},
    })

    aid = asset_id or f"asset_{asset_kind}_1"
    CardAssetRepository(tmp_path).upsert_asset({
        "asset_id": aid,
        "card_id": card_id,
        "asset_kind": asset_kind,
        "source_url": source_url,
        "remote_url": f"https://example.com/{asset_kind}.file",
        "local_path": local_path,
        "mime_type": "image/png" if asset_kind == "image" else "video/mp4",
        "size_bytes": 1024,
        "content_hash": f"hash_{aid}",
        "preservation_class": "cached",
        "status": status,
        "cleanup_state": "present",
        "raw_payload": {},
    })
