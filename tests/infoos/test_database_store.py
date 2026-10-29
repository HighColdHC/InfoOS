import json
import sys
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.infoos._legacy_history import seed_legacy_domain
from backend.infrastructure.storage.database import connect
from tests.infoos.storage.migration_test_helpers import drop_post_v3_schema, migrate_database_path
from backend.application.tasks.repository import TaskRepository
from backend.infrastructure.storage.repositories import (
    CardAssetRepository,
    CardContentBlockRepository,
    CardRepository,
    TopicRepository,
)


def test_initialize_database_creates_core_tables(tmp_path):
    db_path = tmp_path / "data" / "infoos.sqlite3"

    migrate_database_path(db_path)

    with connect(db_path) as conn:
        table_names = {
            row["name"]
            for row in conn.execute(
                "select name from sqlite_master where type in ('table', 'virtual')"
            ).fetchall()
        }
        index_names = {
            row["name"]
            for row in conn.execute("select name from sqlite_master where type = 'index'").fetchall()
        }
        card_columns = {
            row["name"] for row in conn.execute("pragma table_info(cards)").fetchall()
        }

    assert "sources" in table_names
    assert "domains" in table_names
    assert "cards" in table_names
    assert "card_content_blocks" in table_names
    assert "card_assets" in table_names
    assert "topics" in table_names
    assert "run_history" in table_names
    assert "card_ai_insights" in table_names
    assert "card_ai_insight_versions" in table_names
    assert "topic_ai_insights" in table_names
    assert "llm_jobs" in table_names
    assert "notifications" in table_names
    assert "user_comments" in table_names
    assert "reading_queue" in table_names
    assert "materials" in table_names
    assert "topic_writing_context_exports" in table_names
    assert "infoos_search" in table_names
    assert "idx_card_assets_content_hash" in index_names
    assert "idx_cards_active_updated" in index_names
    assert {
        "card_type",
        "version",
        "content_hash",
        "updated_at",
        "status",
        "completeness_status",
        "missing_reasons",
        "processor_version",
        "raw_connector_id",
        "raw_item_id",
        "source_run_id",
    } <= card_columns


def test_information_card_schema_migration_is_idempotent(tmp_path):
    db_path = tmp_path / "data" / "infoos.sqlite3"

    migrate_database_path(db_path)
    migrate_database_path(db_path)

    with connect(db_path) as conn:
        row = conn.execute(
            "select card_type, version, status, completeness_status, missing_reasons from cards limit 1"
        ).fetchone()
        columns = [column["name"] for column in conn.execute("pragma table_info(cards)").fetchall()]

    assert row is None
    assert len(columns) == len(set(columns))


def test_initialize_database_backfills_source_calibration_version(tmp_path):
    db_path = tmp_path / "data" / "infoos.sqlite3"
    migrate_database_path(db_path)
    with connect(db_path) as conn:
        conn.execute(
            """
            insert into sources (
              source_id, platform, source_type, identifier, display_name,
              cadence_hours, connector_status, raw_metadata, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "rss_legacy",
                "rss",
                "feed",
                "https://example.com/feed.xml",
                "Legacy RSS",
                24,
                "implemented_uncalibrated",
                "{}",
                "2026-07-11T08:00:00Z",
                "2026-07-11T08:00:00Z",
            ),
        )
        drop_post_v3_schema(conn)
        conn.execute("pragma user_version=1")
    migrate_database_path(db_path)

    with connect(db_path) as conn:
        row = conn.execute(
            "select calibration_input_version from sources where source_id = 'rss_legacy'"
        ).fetchone()

    assert row is not None
    assert row["calibration_input_version"].startswith("sha256:")
    assert len(row["calibration_input_version"]) == 71


def test_initialize_database_repairs_benign_card_completeness_once(tmp_path):
    db_path = tmp_path / "data" / "infoos.sqlite3"
    migrate_database_path(db_path)
    card_repo = CardRepository(tmp_path)
    for card_id, missing_reasons in (
        ("card_benign_gap", ["skipped_no_media_expected"]),
        ("card_real_gap", ["blocked_no_candidates"]),
    ):
        card_repo.upsert_card(
            {
                "card_id": card_id,
                "source_platform": "rss",
                "source_type": "article",
                "source_url": f"https://example.com/{card_id}",
                "title": card_id,
                "content": "body",
                "captured_at": "2026-07-20T00:00:00Z",
                "processed_at": "2026-07-20T00:00:00Z",
            }
        )
        with connect(db_path) as conn:
            conn.execute(
                """
                update cards set version = 1, content_hash = 'sha256:legacy',
                  status = 'active', completeness_status = 'partial', missing_reasons = ?
                where card_id = ?
                """,
                (json.dumps(missing_reasons), card_id),
            )
    with connect(db_path) as conn:
        conn.execute("delete from settings where key = 'migration.card_completeness.v2'")
        drop_post_v3_schema(conn)
        conn.execute("pragma user_version=1")

    migrate_database_path(db_path)
    migrate_database_path(db_path)

    benign = card_repo.get_card("card_benign_gap")
    real = card_repo.get_card("card_real_gap")
    assert benign is not None and real is not None
    assert benign["completeness_status"] == "complete"
    assert benign["missing_reasons"] == []
    assert benign["version"] == 2
    assert benign["content_hash"].startswith("sha256:")
    assert real["completeness_status"] == "partial"
    assert real["missing_reasons"] == ["blocked_no_candidates"]
    assert real["version"] == 1


def test_initialize_database_backfills_x_titles_and_resolved_no_media_once(tmp_path):
    db_path = tmp_path / "data" / "infoos.sqlite3"
    migrate_database_path(db_path)
    cards = CardRepository(tmp_path)
    cards.upsert_card(
        {
            "card_id": "card_x_legacy_empty",
            "source_platform": "x",
            "source_type": "social_post",
            "source_url": "https://x.com/example/status/legacy",
            "title": "\t\u3000\n",
            "content": "  Legacy   X post. Another sentence.  ",
            "captured_at": "2026-07-20T00:00:00Z",
            "processed_at": "2026-07-20T00:00:00Z",
        }
    )
    cards.upsert_card(
        {
            "card_id": "card_x_title_only",
            "source_platform": "x",
            "source_type": "social_post",
            "source_url": "https://x.com/example/status/title-only",
            "title": "",
            "content": "A complete card also needs a stable title. More body.",
            "captured_at": "2026-07-20T00:00:00Z",
            "processed_at": "2026-07-20T00:00:00Z",
        }
    )
    cards.upsert_card(
        {
            "card_id": "card_x_other_gap",
            "source_platform": "x",
            "source_type": "social_post",
            "source_url": "https://x.com/example/status/other",
            "title": "Existing title",
            "content": "Body",
            "captured_at": "2026-07-20T00:00:00Z",
            "processed_at": "2026-07-20T00:00:00Z",
        }
    )
    cards.upsert_card(
        {
            "card_id": "card_x_stale_zero",
            "source_platform": "x",
            "source_type": "social_post",
            "source_url": "https://x.com/example/status/stale-zero",
            "title": "Latest media evidence wins",
            "content": "Body",
            "captured_at": "2026-07-20T00:00:00Z",
            "processed_at": "2026-07-20T00:00:00Z",
        }
    )
    blocks = CardContentBlockRepository(tmp_path)
    blocks.upsert_block(
        {
            "block_id": "gap_x_legacy_empty",
            "card_id": "card_x_legacy_empty",
            "position": 1,
            "layer": "derived",
            "block_kind": "media_download_gap",
            "status": "blocked",
            "body": "No media found",
            "provenance": "legacy downloader",
            "raw_payload": {"reason": "x_media_download_no_media_found"},
        }
    )
    with connect(db_path) as conn:
        conn.execute(
            """
            update cards set version = 3, content_hash = 'sha256:legacy', status = 'active',
              completeness_status = 'partial', missing_reasons = ? where card_id = ?
            """,
            (json.dumps(["skipped_no_media_found", "x_media_download_no_media_found"]), "card_x_legacy_empty"),
        )
        conn.execute(
            """
            update cards set version = 5, content_hash = 'sha256:other', status = 'active',
              completeness_status = 'partial', missing_reasons = ? where card_id = ?
            """,
            (json.dumps(["x_media_download_no_media_found", "auth_required"]), "card_x_other_gap"),
        )
        conn.execute(
            """
            update cards set version = 7, content_hash = 'sha256:stale-zero', status = 'active',
              completeness_status = 'partial', missing_reasons = ? where card_id = ?
            """,
            (json.dumps(["skipped_no_media_found", "x_media_download_no_media_found"]), "card_x_stale_zero"),
        )
        conn.execute(
            """
            update cards set version = 2, content_hash = 'sha256:title-old', status = 'active',
              completeness_status = 'complete', missing_reasons = '[]' where card_id = ?
            """,
            ("card_x_title_only",),
        )
        conn.execute(
            """
            insert into tasks(task_id, task_type, target_kind, target_id, status,
              artifacts_summary, created_at, updated_at)
            values (?, 'media_download', 'card', ?, 'skipped', ?, ?, ?)
            """,
            (
                "media_download_card_x_legacy_empty",
                "card_x_legacy_empty",
                json.dumps({
                    "download_status": "skipped_no_media_found",
                    "candidate_count": 0,
                    "gap_reasons": ["x_media_download_no_media_found"],
                }),
                "2026-07-20T00:00:00Z",
                "2026-07-20T00:00:00Z",
            ),
        )
        conn.execute(
            """
            insert into tasks(task_id, task_type, target_kind, target_id, status,
              artifacts_summary, created_at, updated_at)
            values (?, 'media_download', 'card', ?, 'skipped', ?, ?, ?)
            """,
            (
                "media_download_card_x_other_gap",
                "card_x_other_gap",
                json.dumps({
                    "download_status": "skipped_no_media_found",
                    "candidate_count": 5,
                    "gap_reasons": ["x_media_download_no_media_found"],
                }),
                "2026-07-20T00:00:00Z",
                "2026-07-20T00:00:00Z",
            ),
        )
        for task_id, candidate_count, created_at in (
            ("media_download_card_x_stale_zero_old", 0, "2026-07-20T00:00:00Z"),
            ("media_download_card_x_stale_zero_new", 5, "2026-07-21T00:00:00Z"),
        ):
            conn.execute(
                """
                insert into tasks(task_id, task_type, target_kind, target_id, status,
                  artifacts_summary, created_at, updated_at)
                values (?, 'media_download', 'card', ?, 'skipped', ?, ?, ?)
                """,
                (
                    task_id,
                    "card_x_stale_zero",
                    json.dumps({
                        "download_status": "skipped_no_media_found",
                        "candidate_count": candidate_count,
                        "gap_reasons": ["x_media_download_no_media_found"],
                    }),
                    created_at,
                    created_at,
                ),
            )
        conn.execute(
            """
            insert into tasks(task_id, task_type, target_kind, target_id, status,
              artifacts_summary, created_at, updated_at)
            values (?, 'media_download', 'card', ?, 'skipped', ?, ?, ?)
            """,
            (
                "media_download_card_x_stale_zero_retrying",
                "card_x_stale_zero",
                json.dumps({
                    "download_status": "skipped_no_media_found",
                    "candidate_count": 0,
                    "gap_reasons": ["x_media_download_no_media_found"],
                }),
                "2026-07-22T00:00:00Z",
                "2026-07-22T00:00:00Z",
            ),
        )
        conn.execute("delete from settings where key = 'migration.x_card_enrichment.v1'")
        drop_post_v3_schema(conn)
        conn.execute("pragma user_version=1")

    assert TaskRepository.for_base_dir(tmp_path).retry(
        "media_download_card_x_stale_zero_retrying"
    ) is True

    migrate_database_path(db_path)
    migrate_database_path(db_path)

    repaired = cards.get_card("card_x_legacy_empty")
    untouched = cards.get_card("card_x_other_gap")
    stale_zero = cards.get_card("card_x_stale_zero")
    title_only = cards.get_card("card_x_title_only")
    gap = blocks.list_for_card("card_x_legacy_empty")[0]
    assert repaired["title"] == "Legacy X post."
    assert repaired["completeness_status"] == "complete"
    assert repaired["missing_reasons"] == []
    assert repaired["version"] == 4
    assert repaired["content_hash"].startswith("sha256:")
    assert gap["status"] == "resolved"
    assert gap["raw_payload"]["resolved"] is True
    assert untouched["title"] == "Existing title"
    assert untouched["missing_reasons"] == ["x_media_download_no_media_found", "auth_required"]
    assert untouched["version"] == 5
    assert stale_zero["completeness_status"] == "partial"
    assert stale_zero["missing_reasons"] == ["skipped_no_media_found", "x_media_download_no_media_found"]
    assert stale_zero["version"] == 7
    assert title_only["title"] == "A complete card also needs a stable title."
    assert title_only["completeness_status"] == "complete"
    assert title_only["version"] == 3
    assert title_only["content_hash"] != "sha256:title-old"
    with connect(db_path) as conn:
        indexed = conn.execute(
            "select title from cards where card_id = ? and rowid in "
            "(select rowid from card_text_fts where card_text_fts match ?)",
            ("card_x_title_only", '"' + title_only["title"].replace('"', '""') + '"'),
        ).fetchone()
    assert indexed["title"] == title_only["title"]


def test_database_connect_closes_after_context_exit(tmp_path):
    db_path = tmp_path / "data" / "infoos.sqlite3"
    migrate_database_path(db_path)

    with connect(db_path) as conn:
        conn.execute("select 1").fetchone()

    try:
        conn.execute("select 1").fetchone()
    except sqlite3.ProgrammingError as exc:
        assert "closed" in str(exc)
    else:
        raise AssertionError("database connection remained open after context exit")


def test_domain_card_topic_round_trip(tmp_path):
    domain_id = seed_legacy_domain(tmp_path, "AI", sort_order=1)

    cards = CardRepository(tmp_path)
    cards.upsert_card(
        {
            "card_id": "card_ai",
            "source_id": None,
            "source_platform": "x",
            "source_type": "social_post",
            "source_url": "https://x.com/example/status/1",
            "source_author": "example",
            "title": "Agent update",
            "content": "A new agent workflow is shipping today.",
            "summary": "Agent workflow shipping.",
            "primary_domain_id": domain_id,
            "secondary_domain_ids": [],
            "published_at": "2026-05-08T01:00:00Z",
            "captured_at": "2026-05-08T01:01:00Z",
            "processed_at": "2026-05-08T01:02:00Z",
            "raw_payload": {},
        }
    )

    topics = TopicRepository(tmp_path)
    topics.upsert_topic(
        {
            "topic_id": "topic_agents",
            "title": "Agent workflow launch",
            "latest_progress": "New workflow shipped",
            "ai_interpretation": "This is relevant to AI builders.",
            "primary_domain_id": domain_id,
            "secondary_domain_ids": [],
            "status": "刚出现",
            "source_summary": "example + 1 source",
        }
    )
    topics.attach_card("topic_agents", "card_ai")

    assert cards.get_card("card_ai")["primary_domain_id"] == domain_id
    assert topics.list_topics(domain_id=domain_id)[0]["topic_id"] == "topic_agents"


def test_card_rich_content_blocks_and_assets_round_trip(tmp_path):
    domain_id = seed_legacy_domain(tmp_path, "AI")
    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card_x_rich",
            "source_id": None,
            "source_platform": "x",
            "source_type": "social_post",
            "source_url": "https://x.com/example/status/2",
            "source_author": "example",
            "title": "Rich X post",
            "content": "Original tweet text",
            "summary": "",
            "primary_domain_id": domain_id,
            "secondary_domain_ids": [],
            "published_at": "2026-05-22T01:00:00Z",
            "captured_at": "2026-05-22T01:01:00Z",
            "processed_at": "2026-05-22T01:02:00Z",
            "raw_payload": {},
        }
    )

    blocks = CardContentBlockRepository(tmp_path)
    blocks.upsert_block(
        {
            "block_id": "block_tweet_original",
            "card_id": "card_x_rich",
            "position": 20,
            "layer": "original",
            "block_kind": "tweet",
            "status": "preserved",
            "body": "Original tweet text",
            "segments": [{"kind": "text", "text": "Original tweet text"}],
            "source_url": "https://x.com/example/status/2",
            "provenance": "x",
            "raw_payload": {"tweet_id": "2"},
        }
    )
    blocks.upsert_block(
        {
            "block_id": "block_tweet_context",
            "card_id": "card_x_rich",
            "position": 10,
            "layer": "context",
            "block_kind": "quoted_tweet",
            "status": "preserved",
            "body": "Quoted context",
            "segments": [],
            "source_url": "https://x.com/context/status/1",
            "provenance": "x",
            "raw_payload": {"tweet_id": "1"},
        }
    )
    assets = CardAssetRepository(tmp_path)
    assets.upsert_asset(
        {
            "asset_id": "asset_x_video",
            "card_id": "card_x_rich",
            "asset_kind": "video",
            "source_url": "https://x.com/example/status/2",
            "remote_url": "https://video.twimg.com/example.mp4",
            "local_path": "data/assets/card_x_rich/video.mp4",
            "mime_type": "video/mp4",
            "size_bytes": 1024,
            "content_hash": "sha256:rich-video",
            "preservation_class": "archived",
            "status": "ready",
            "cleanup_state": "present",
            "raw_payload": {"variant": "mp4"},
        }
    )

    listed_blocks = blocks.list_for_card("card_x_rich")
    block = listed_blocks[1]
    listed_asset = assets.list_for_card("card_x_rich")[0]
    asset = assets.get_asset("asset_x_video")

    assert [block["block_id"] for block in listed_blocks] == ["block_tweet_context", "block_tweet_original"]
    assert block["position"] == 20
    assert block["body"] == "Original tweet text"
    assert block["segments"] == [{"kind": "text", "text": "Original tweet text"}]
    assert listed_asset["local_path"] == "data/assets/card_x_rich/video.mp4"
    assert listed_asset["content_hash"] == "sha256:rich-video"
    assert asset is not None
    assert asset["local_path"] == "data/assets/card_x_rich/video.mp4"
    assert asset["content_hash"] == "sha256:rich-video"
    assert asset["raw_payload"] == {"variant": "mp4"}


def test_card_repository_exposes_nested_content_quality_metadata(tmp_path):
    domain_id = seed_legacy_domain(tmp_path, "AI")
    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card_thin",
            "source_id": None,
            "source_platform": "rss",
            "source_type": "article",
            "source_url": "https://example.com/thin",
            "source_author": None,
            "title": "Thin item",
            "content": "Thin item\n\nComments",
            "summary": "Thin item",
            "primary_domain_id": domain_id,
            "secondary_domain_ids": [],
            "published_at": "2026-05-08T01:00:00Z",
            "captured_at": "2026-05-08T01:01:00Z",
            "processed_at": "2026-05-08T01:02:00Z",
            "raw_payload": {
                "raw_payload": {
                    "content_quality": "thin",
                    "content_needs_enrichment": True,
                    "enrichment_applied": False,
                }
            },
        }
    )

    card = CardRepository(tmp_path).get_card("card_thin")

    assert card["content_quality"] == "thin"
    assert card["content_needs_enrichment"] is True


def test_cards_persist_sort_keys_for_different_timestamp_formats(tmp_path):
    domain_id = seed_legacy_domain(tmp_path, "AI", sort_order=1)
    cards = CardRepository(tmp_path)
    for payload in [
        {
            "card_id": "card_twitter_legacy_date",
            "source_platform": "x",
            "source_type": "social_post",
            "title": "Old X card with Twitter date",
            "published_at": "Wed May 06 01:47:55 +0000 2026",
        },
        {
            "card_id": "card_iso_newer",
            "source_platform": "rss",
            "source_type": "article",
            "title": "Newer RSS card",
            "published_at": "2026-05-10T18:09:39-05:00",
        },
    ]:
        cards.upsert_card(
            {
                "source_id": None,
                "source_url": "",
                "source_author": None,
                "content": payload["title"],
                "summary": payload["title"],
                "primary_domain_id": domain_id,
                "secondary_domain_ids": [],
                "captured_at": payload["published_at"],
                "processed_at": payload["published_at"],
                "raw_payload": {},
                **payload,
            }
        )

    with cards.connect_existing() as conn:
        rows = conn.execute(
            "select card_id from cards where primary_domain_id = ? "
            "order by sort_ts desc, card_id desc",
            (domain_id,),
        ).fetchall()
    assert [row["card_id"] for row in rows] == ["card_iso_newer", "card_twitter_legacy_date"]
