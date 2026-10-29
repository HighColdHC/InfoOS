"""Search ranking must not hide unseen rows behind the previous first result."""
from __future__ import annotations

import pytest

from backend.domain.sources.configuration_projection import database_source_to_connector_config
from backend.infrastructure.connectors.opencli import OpenCLIConnector


def connector(metadata=None):
    config = database_source_to_connector_config({
        "source_id": "keyword", "connector_id": "keyword", "platform": "zsxq",
        "source_type": "keyword", "identifier": "网站出海", "max_items_per_run": 20,
        "raw_metadata": metadata or {},
    })
    return OpenCLIConnector(**{
        key: value for key, value in config.items() if key in OpenCLIConnector.__dataclass_fields__
    })


def row(identity):
    return {"topic_id": identity, "url": f"https://wx.zsxq.com/topic/{identity}",
            "title": "网站出海", "full_text": "网站出海方法", "is_digest": False}


def test_comprehensive_search_accepts_unseen_row_after_legacy_cursor(monkeypatch):
    search = connector()
    records = [row("old-head"), row("unseen-middle"), row("old-tail")]
    monkeypatch.setattr(search, "_load_payload", lambda: records)

    # The deployed scalar cursor points at the unchanged first search result.
    migrated = search._fetch_new_items_in_lease("old-head")
    assert "unseen-middle" in {item.raw_item_id for item in migrated.items}

    # Reordering the same ranked results does not replay them.
    records[:] = [records[2], records[0], records[1]]
    repeated = search._fetch_new_items_in_lease(migrated.next_cursor)
    assert repeated.items == []
    assert repeated.visible_count == repeated.dedup_skipped_count == 3

    # An unseen result below the old first result must still enter the pipeline.
    records.append(row("later-unseen"))
    fresh = search._fetch_new_items_in_lease(repeated.next_cursor)
    assert [item.raw_item_id for item in fresh.items] == ["later-unseen"]
    assert fresh.new_count == 1


@pytest.mark.parametrize("sort,expected", [
    ("newest", "newest_first"), ("earliest", "oldest_first"),
    ("comprehensive", "snapshot"), ("most-favorited", "snapshot"),
    ("most-commented", "snapshot"), ("most-liked", "snapshot"),
    ("most-coins", "snapshot"), ("recent-coins", "snapshot"),
])
def test_search_cursor_follows_explicit_sort(sort, expected):
    for key in ("zsxq_search", "scys_search"):
        assert connector({key: {"sort": sort}}).result_order == expected
