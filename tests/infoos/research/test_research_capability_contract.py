from __future__ import annotations

from backend.domain.sources.registry import capability_catalog, configurable_platforms
from tests.infoos.storage.migration_test_helpers import migrate_database_path
from backend.infrastructure.storage.paths import resolve_sqlite_path
from backend.infrastructure.storage.sources import SourceRepository


def test_every_configurable_platform_declares_research_capability() -> None:
    catalog = {
        row["platform"]: row
        for row in capability_catalog()
        if row["configurable"]
    }

    assert set(catalog) == {entry.platform for entry in configurable_platforms()}
    assert len(catalog) == 10
    assert all(row["research"] is not None for row in catalog.values())
    assert catalog["rss"]["research"]["requires_source_selection"] is True
    assert catalog["producthunt"]["research"]["status"] == "limited"


def test_research_runtime_sources_are_hidden_from_maintained_source_lists(tmp_path) -> None:
    migrate_database_path(resolve_sqlite_path(tmp_path))
    repo = SourceRepository(tmp_path)
    common = {
        "platform": "rss",
        "source_type": "feed",
        "identifier": "https://example.com/feed.xml",
        "cadence_hours": 1,
        "enabled": True,
        "connector_status": "supported",
    }
    repo.upsert_source({**common, "source_id": "maintained", "display_name": "Maintained"})
    repo.upsert_source(
        {
            **common,
            "source_id": "research",
            "display_name": "Research",
            "source_scope": "research",
        }
    )

    assert [source["source_id"] for source in repo.list_sources()] == ["maintained"]
    assert {source["source_id"] for source in repo.list_sources(include_research=True)} == {
        "maintained",
        "research",
    }
    assert repo.get_source("research")["source_scope"] == "research"


def test_registry_payload_declares_sort_options_and_platform_defaults():
    catalog = {row["platform"]: row["research"] for row in capability_catalog() if row["research"]}
    expected = {
        "xiaohongshu": (["general", "latest", "most-liked", "most-commented", "most-collected"], "general"),
        "douyin": (["general", "latest", "most-liked"], "general"),
        "zsxq": (["latest", "earliest"], "latest"),
    }
    for platform, research in catalog.items():
        options, default = expected.get(platform, (["latest"], "latest"))
        assert research["sort_options"] == options
        assert research["default_sort"] == default
