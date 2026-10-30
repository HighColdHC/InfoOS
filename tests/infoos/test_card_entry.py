"""Display fields shared by card lists, briefs and opportunity projections."""
import pytest

from backend.infrastructure.storage.card_entry import card_display_fields


@pytest.mark.parametrize("platform,title,content,payload,expected_title,is_digest", [
    ("zsxq", "精华帖标题", "精华帖正文", {"raw_payload": {"is_digest": True}}, "精华帖标题", True),
    ("x", "Opus 5.", "Opus 5.5 is available. More details.", {}, "Opus 5.5 is available.", None),
])
def test_card_display_fields(platform, title, content, payload, expected_title, is_digest):
    assert card_display_fields(platform, title, content, payload) == {
        "title": expected_title, "is_digest": is_digest,
    }
