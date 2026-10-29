import pytest

from backend.application.pipeline.information_card_builder import build_information_card
from backend.domain.card_titles import body_title, broken_body_title, display_title, information_card_title
from backend.domain.models.normalized_item import NormalizedItem
from backend.domain.models.raw_item import RawItem


def test_x_blank_title_uses_first_normalized_sentence_and_limits_unicode_length() -> None:
    content = "  第一段有   多余空白。 后续句子不应进入标题。\n\n第二段。" + "字" * 100

    assert information_card_title("x", " \t", content) == "第一段有 多余空白。"
    assert information_card_title("x", "", "字" * 81) == "字" * 79 + "…"


def test_title_helper_preserves_nonblank_source_title_and_other_platform_blanks() -> None:
    assert information_card_title("x", "  Source   title  ", "fallback") == "Source title"
    assert information_card_title("rss", "", "fallback") == ""


def test_information_card_builder_applies_x_title_fallback() -> None:
    raw = RawItem(
        raw_item_id="x-1",
        connector_id="x_user_example",
        source_platform="x",
        source_type="social_post",
        source_url="https://x.com/example/status/1",
        captured_at="2026-07-22T00:00:00Z",
        raw_text="First sentence. Second sentence.",
        raw_metadata={},
    )
    normalized = NormalizedItem(
        raw_item_id=raw.raw_item_id,
        source_url=raw.source_url,
        source_author="example",
        title="",
        normalized_content="First sentence. Second sentence.",
        summary="",
    )

    assert build_information_card(normalized, raw).title == "First sentence."


@pytest.mark.parametrize("old,content,expected", [
    ("Opus 5.", "Opus 5.5 剪辑真的做的不错……", "Opus 5.5 剪辑真的做的不错……"),
    ("OpenAI 发布 GPT-6.", "OpenAI 发布 GPT-6.1 Sol：能力接近……", "OpenAI 发布 GPT-6.1 Sol：能力接近……"),
    ("Sonnet 5.", "Sonnet 5.5 发布了……", "Sonnet 5.5 发布了……"),
    ("华尔街日报：OpenAI 叫停 GPT-6.", "华尔街日报：OpenAI 叫停 GPT-6.1 Astra，因为……", "华尔街日报：OpenAI 叫停 GPT-6.1 Astra，因为……"),
    ("RT @SpaceXAI: Grok 4.", "RT @SpaceXAI: Grok 4.7 is now on Amazon Bedrock", "RT @SpaceXAI: Grok 4.7 is now on Amazon Bedrock"),
    ("Hi,", "Hi,\n\nTomorrow we are re-opening the Pro $200 subscriptions……", "Tomorrow we are re-opening the Pro $200 subscriptions……"),
    ("https://t.", "https://t.co/abc123", "https://t.co/abc123"),
])
def test_reported_x_titles(old, content, expected):
    assert information_card_title("x", "", content) == expected
    assert broken_body_title(old, content)
    assert display_title("x", old, content) == expected
    assert body_title(expected) == expected


@pytest.mark.parametrize("content,expected", [
    ("中文正常的第一句。后面还有第二句。", "中文正常的第一句。"),
    ("这款模型发布了吗？现在已经发布了！", "这款模型发布了吗？"),
    ("模型表现真的不错！下一句。", "模型表现真的不错！"),
    ("Hi, Tomorrow we are opening subscriptions.", "Tomorrow we are opening subscriptions."),
    ("Hello!\nhttps://t.co/abc\nUseful announcement here.", "Useful announcement here."),
    ("短句。真正有用的后续内容。", "真正有用的后续内容。"),
    ("See example.com and https://t.co/abc?x=1. More news.", "See example.com and https://t.co/abc?x=1."),
    ("Dr. Smith works in the U.S. on AI, e.g. agents. Next sentence.", "Dr. Smith works in the U.S. on AI, e.g. agents."),
    ("Hi,", "Hi,"),
    ("https://t.co/abc", "https://t.co/abc"),
])
def test_body_title_boundaries(content, expected):
    assert body_title(content) == expected


def test_long_titles_cut_at_word_or_character():
    assert body_title("字" * 81) == "字" * 79 + "…"
    assert body_title("Useful title with a longerword", max_length=25) == "Useful title with a…"
    assert len(body_title("https://example.com/" + "a" * 100)) <= 80
    assert body_title("abc", max_length=1) == "…"
    assert body_title("") == ""


def test_reported_retweet_with_nonprefix_title_is_not_a_repair_candidate():
    content = "RT @SpaceXAI: Grok 4.7 is now on Amazon Bedrock"
    assert body_title(content) == content
    assert not broken_body_title("Grok 4.", content)


@pytest.mark.parametrize("platform,title,content", [
    ("rss", "Opus 5.", "Opus 5.5 is available."),
    ("x", "Useful announcement.", "Useful announcement. More details."),
    ("x", "Grok 4.", "RT @SpaceXAI: Grok 4.7 is available."),
    ("x", "  Explicit source title  ", "Other body."),
])
def test_display_title_preserves_other_titles(platform, title, content):
    assert display_title(platform, title, content) == title
