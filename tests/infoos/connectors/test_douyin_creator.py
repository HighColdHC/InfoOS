from backend.infrastructure.connectors.douyin.creator import (
    CURSOR_HISTORY_SIZE,
    DouyinCreatorConnector,
    _cursor_aweme_ids,
)
from backend.infrastructure.connectors.opencli import OpenCLIConnector
import pytest


def test_creator_projection_constructs_canonical_url_and_explicit_gaps():
    connector = DouyinCreatorConnector(
        connector_id="douyin_creator",
        command=["douyin", "user-videos", "sec_uid", "--limit", "1"],
        source_platform="douyin",
        source_type="video",
        result_order="newest_first",
        field_mapping={
            "id": "url",
            "url": "url",
            "text": "title",
            "author_name": "author",
            "published_at": "published_at",
        },
        metadata_mapping={
            "aweme_id": "aweme_id",
            "duration": "duration",
            "likes": "digg_count",
            "play_url": "play_url",
            "top_comments": "top_comments",
        },
    )

    records = connector._extract_records(
        [{
            "aweme_id": "9000000000000000005",
            "title": "标题",
            "duration": 12,
            "digg_count": 9,
            "play_url": "https://video.example/play",
            "top_comments": [{"text": "读者热评"}],
        }]
    )
    raw = connector._to_raw_item(records[0])

    assert raw.raw_item_id == "https://www.douyin.com/video/9000000000000000005"
    assert raw.source_url == raw.raw_item_id
    assert raw.raw_author.name is None
    assert raw.raw_timestamps.published_at is None
    assert raw.raw_metadata["likes"] == 9
    assert raw.raw_metadata["top_comments"] == [{"text": "读者热评"}]
    assert "读者热评" not in raw.raw_text
    assert raw.raw_metadata["opencli_record"]["is_top"] is False


def test_creator_fetch_uses_bounded_initial_fetch_then_stops_at_infoos_cursor(monkeypatch):
    commands = []
    payload = [
        {
            "aweme_id": "9000000000000000006",
            "title": "新视频",
            "play_url": "https://video.example/new",
            "author": "作者",
            "author_handle": "author-id",
            "author_url": "https://www.douyin.com/user/sec_uid",
            "published_at": "2026-07-24T08:00:00.000Z",
        },
        {
            "aweme_id": "9000000000000000005",
            "title": "游标视频",
            "play_url": "https://video.example/cursor",
        },
    ]

    def fake_load(self, command_template=None):
        commands.append(list(self.command))
        return payload

    monkeypatch.setattr(OpenCLIConnector, "_load_payload", fake_load)
    connector = DouyinCreatorConnector(
        connector_id="douyin_creator",
        command=[
            "douyin", "user-videos", "sec_uid", "--limit", "9",
            "--max_pages", "25", "--with_comments", "false",
        ],
        source_platform="douyin",
        source_type="video",
        result_order="newest_first",
        field_mapping={
            "id": "url",
            "url": "url",
            "text": "title",
            "author_name": "author",
            "author_handle": "author_handle",
            "author_url": "author_url",
            "published_at": "published_at",
        },
        metadata_mapping={"play_url": "play_url"},
    )

    initial = connector._fetch_new_items_in_lease(None)
    initial_pagination = connector.discovery_evidence["pagination"]
    incremental = connector._fetch_new_items_in_lease(
        "https://www.douyin.com/video/9000000000000000005"
    )

    assert "--stop_aweme_id" not in commands[0]
    assert commands[0][commands[0].index("--limit") + 1] == "9"
    assert commands[0][commands[0].index("--max_pages") + 1] == "25"
    assert initial_pagination == "bounded_initial"
    assert commands[1][-2:] == ["--stop_aweme_id", "9000000000000000005"]
    assert commands[1][commands[1].index("--limit") + 1] == "9"
    assert commands[1][commands[1].index("--max_pages") + 1] == "25"
    assert [item.raw_text for item in initial.items] == ["新视频", "游标视频"]
    assert [item.raw_text for item in incremental.items] == ["新视频"]
    assert _cursor_aweme_ids(incremental.next_cursor) == [
        "9000000000000000006", "9000000000000000005",
    ]
    assert incremental.items[0].raw_author.name == "作者"
    assert connector.discovery_evidence["pagination"] == "until_cursor"


def test_creator_burst_defers_newest_items_without_skipping_cursor_gap(monkeypatch):
    payload = [
        {
            "aweme_id": str(aweme_id),
            "title": f"视频 {aweme_id}",
            "play_url": f"https://video.example/{aweme_id}",
        }
        for aweme_id in (105, 104, 103, 102, 101, 100)
    ]
    commands: list[list[str]] = []

    def fake_load(self, command_template=None):
        commands.append(list(self.command))
        return payload

    monkeypatch.setattr(OpenCLIConnector, "_load_payload", fake_load)
    connector = DouyinCreatorConnector(
        connector_id="douyin_creator",
        command=[
            "douyin", "user-videos", "sec_uid", "--limit", "2",
            "--max_pages", "25", "--with_comments", "false",
        ],
        source_platform="douyin",
        source_type="video",
        result_order="newest_first",
        field_mapping={"id": "url", "url": "url", "text": "title"},
        metadata_mapping={"play_url": "play_url"},
    )

    result = connector._fetch_new_items_in_lease(
        "https://www.douyin.com/video/100"
    )

    assert commands[0][commands[0].index("--limit") + 1] == "2"
    assert commands[0][-2:] == ["--stop_aweme_id", "100"]
    assert [item.raw_item_id for item in result.items] == [
        "https://www.douyin.com/video/102",
        "https://www.douyin.com/video/101",
    ]
    assert _cursor_aweme_ids(result.next_cursor) == ["102", "101", "100"]
    assert connector.discovery_evidence["discovered_link_count"] == 6
    assert connector.discovery_evidence["deferred_new_item_count"] == 3


def test_creator_cursor_reads_legacy_and_writes_ordered_five_id_history(monkeypatch):
    payload = [{"aweme_id": str(id), "title": str(id)} for id in (110, 109, 108, 107, 106, 105)]
    commands = []

    def fake_load(self, command_template=None):
        commands.append(list(self.command))
        return payload

    monkeypatch.setattr(OpenCLIConnector, "_load_payload", fake_load)
    connector = DouyinCreatorConnector(
        connector_id="douyin_creator", command=["douyin", "user-videos", "sec_uid", "--limit", "10"],
        source_platform="douyin", source_type="video", result_order="newest_first",
        field_mapping={"id": "url", "url": "url", "text": "title"},
    )
    initial = connector._fetch_new_items_in_lease(None)
    assert _cursor_aweme_ids(initial.next_cursor) == ["110", "109", "108", "107", "106"]
    assert len(_cursor_aweme_ids(initial.next_cursor)) == CURSOR_HISTORY_SIZE

    incremental = connector._fetch_new_items_in_lease(initial.next_cursor)
    assert commands[1][-2:] == ["--stop_aweme_id", "110,109,108,107,106"]
    assert incremental.items == []
    assert _cursor_aweme_ids(incremental.next_cursor) == ["110", "109", "108", "107", "106"]
    assert _cursor_aweme_ids("https://www.douyin.com/video/100") == ["100"]


def test_creator_missing_stop_reinitializes_from_bounded_adapter_rows(monkeypatch):
    payload = [
        {"aweme_id": str(id), "title": str(id), "cursor_reinitialized": True}
        for id in (110, 109, 108)
    ]
    monkeypatch.setattr(OpenCLIConnector, "_load_payload", lambda self, command_template=None: payload)
    connector = DouyinCreatorConnector(
        connector_id="douyin_creator", command=["douyin", "user-videos", "sec_uid", "--limit", "3"],
        source_platform="douyin", source_type="video", result_order="newest_first",
        field_mapping={"id": "url", "url": "url", "text": "title"},
    )
    result = connector._fetch_new_items_in_lease("https://www.douyin.com/video/100")
    assert [item.raw_item_id for item in result.items] == [
        "https://www.douyin.com/video/110",
        "https://www.douyin.com/video/109",
        "https://www.douyin.com/video/108",
    ]
    assert _cursor_aweme_ids(result.next_cursor) == ["110", "109", "108"]
    assert connector.cursor_reinitialized is True
    assert connector.discovery_evidence["old_cursor_id_count"] == 1


def test_creator_reinitialized_cursor_drops_all_old_stop_ids(monkeypatch):
    monkeypatch.setattr(
        OpenCLIConnector,
        "_load_payload",
        lambda self, command_template=None: [
            {"aweme_id": id, "title": id, "cursor_reinitialized": True}
            for id in ("210", "209")
        ],
    )
    connector = DouyinCreatorConnector(
        connector_id="douyin_creator",
        command=["douyin", "user-videos", "sec_uid", "--limit", "2"],
        source_platform="douyin", source_type="video", result_order="newest_first",
        field_mapping={"id": "url", "url": "url", "text": "title"},
    )
    old_cursor = 'douyin-creator-v1:["105","104","103","102","101"]'

    result = connector._fetch_new_items_in_lease(old_cursor)

    assert _cursor_aweme_ids(result.next_cursor) == ["210", "209"]
    assert connector.discovery_evidence["old_cursor_id_count"] == 5


def _pinned_connector(monkeypatch, rows, limit=20):
    monkeypatch.setattr(OpenCLIConnector, "_load_payload", lambda self, command_template=None: rows)
    return DouyinCreatorConnector(
        connector_id="douyin_creator", command=["douyin", "user-videos", "sec_uid", "--limit", str(limit)],
        source_platform="douyin", source_type="video", result_order="newest_first",
        field_mapping={"id": "url", "url": "url", "text": "title"},
    )


def _rows(ids, pinned=(), reinitialized=False):
    return [
        {"aweme_id": str(id), "title": str(id), "is_top": id in pinned,
         "cursor_reinitialized": reinitialized}
        for id in ids
    ]


def test_mixed_pinned_cursor_self_heals_and_limits_only_regular_new_items(monkeypatch):
    connector = _pinned_connector(monkeypatch, _rows([1, 2, 105, 104, 103, 102, 101, 100, 99], pinned=[1, 2]), 2)
    result = connector._fetch_new_items_in_lease('douyin-creator-v1:["1","100","99"]')

    assert [item.raw_text for item in result.items] == ["2", "102", "101"]
    assert result.items[0].raw_metadata["opencli_record"]["is_top"] is True
    assert result.new_count == 3
    assert result.visible_count == 9
    assert result.dedup_skipped_count == 6
    assert connector.discovery_evidence["pinned_item_count"] == 2
    assert connector.discovery_evidence["deferred_new_item_count"] == 3
    assert _cursor_aweme_ids(result.next_cursor) == ["102", "101", "100", "99", "1", "2"]


def test_cursor_keeps_five_regular_ids_and_three_pins_with_unobserved_old_ids_regular(monkeypatch):
    connector = _pinned_connector(monkeypatch, _rows([1, 2, 3, 110, 109, 108, 107], pinned=[1, 2, 3]))
    old_cursor = 'douyin-creator-v1:["1","107","106","105","104","2","3","4"]'
    result = connector._fetch_new_items_in_lease(old_cursor)

    assert [item.raw_text for item in result.items] == ["110", "109", "108"]
    assert _cursor_aweme_ids(result.next_cursor) == ["110", "109", "108", "107", "106", "1", "2", "3"]
    assert len(_cursor_aweme_ids(result.next_cursor)) == 8
    assert connector.discovery_evidence["old_cursor_id_count"] == 8

    connector = _pinned_connector(monkeypatch, _rows([1, 110, 109], pinned=[1]))
    result = connector._fetch_new_items_in_lease('douyin-creator-v1:["1","100","2"]')
    assert _cursor_aweme_ids(result.next_cursor) == ["110", "109", "100", "2", "1"]


def test_only_pinned_cursor_reinitializes_without_merging_old_ids(monkeypatch):
    connector = _pinned_connector(monkeypatch, _rows([1, 110, 109], pinned=[1], reinitialized=True), 3)
    result = connector._fetch_new_items_in_lease('douyin-creator-v1:["1"]')

    assert [item.raw_text for item in result.items] == ["1", "110", "109"]
    assert connector.cursor_reinitialized is True
    assert _cursor_aweme_ids(result.next_cursor) == ["110", "109", "1"]
    assert connector.discovery_evidence["pinned_item_count"] == 1

    connector = _pinned_connector(monkeypatch, _rows([2, 110], pinned=[2], reinitialized=True), 2)
    result = connector._fetch_new_items_in_lease('douyin-creator-v1:["1"]')
    assert _cursor_aweme_ids(result.next_cursor) == ["110", "2"]


def test_known_newest_pinned_item_does_not_report_new_items(monkeypatch):
    connector = _pinned_connector(monkeypatch, _rows([110, 109], pinned=[110]))
    result = connector._fetch_new_items_in_lease('douyin-creator-v1:["110","109"]')
    assert result.items == []
    assert result.new_count == 0
    assert _cursor_aweme_ids(result.next_cursor) == ["109", "110"]


@pytest.mark.parametrize("reinitialized", [False, True])
def test_initial_and_reinitialized_cursor_reserve_pin_slots(monkeypatch, reinitialized):
    connector = _pinned_connector(
        monkeypatch, _rows([1, 2, 3, 110, 109, 108, 107, 106, 105], pinned=[1, 2, 3], reinitialized=reinitialized),
    )
    result = connector._fetch_new_items_in_lease('douyin-creator-v1:["999"]' if reinitialized else None)
    assert len(result.items) == 9
    assert _cursor_aweme_ids(result.next_cursor) == ["110", "109", "108", "107", "106", "1", "2", "3"]
