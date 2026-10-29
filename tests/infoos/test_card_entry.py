"""Display fields shared by card lists, briefs and opportunity projections."""
import pytest

from backend.infrastructure.storage.card_entry import card_display_fields


@pytest.mark.parametrize("platform,title,content,payload,expected_title,is_digest", [
    ("zsxq", "精华帖标题", "精华帖正文", {"raw_payload": {"is_digest": True}}, "精华帖标题", True),
    ("zsxq", "普通帖标题", "普通帖正文", {"raw_payload": {"is_digest": False}}, "普通帖标题", False),
    ("zsxq", "历史帖标题", "历史帖正文", {}, "历史帖标题", None),
    ("x", "Opus 5.", "Opus 5.5 is available. More details.", {}, "Opus 5.5 is available.", None),
    ("rss", "来源标题", "正文内容", {}, "来源标题", None),
])
def test_card_display_fields(platform, title, content, payload, expected_title, is_digest):
    assert card_display_fields(platform, title, content, payload) == {
        "title": expected_title, "is_digest": is_digest,
    }
