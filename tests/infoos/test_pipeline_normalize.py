import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.domain.models.raw_item import RawAuthor, RawItem, RawTimestamps
from backend.application.pipeline.normalize import normalize_raw_item


def _raw_item(title=None, url="https://x.com/alice/status/1", text="hello world"):
    metadata = {} if title is None else {"title": title}
    return RawItem(
        raw_item_id="ri-1",
        connector_id="x_test",
        source_platform="x",
        source_type="social_post",
        source_url=url,
        captured_at="2026-04-19T10:00:00Z",
        raw_text=text,
        raw_author=RawAuthor(name="Alice", handle="alice"),
        raw_timestamps=RawTimestamps(published_at="2026-04-19T10:00:00Z"),
        raw_metadata=metadata,
    )


def test_normalize_keeps_explicit_title():
    item = _raw_item(title="Real title", text="body content here")
    normalized = normalize_raw_item(item)
    assert normalized.title == "Real title"


def test_normalize_returns_empty_title_when_metadata_has_none():
    item = _raw_item(title=None, text="body content here")
    normalized = normalize_raw_item(item)
    assert normalized.title == ""


def test_normalize_returns_empty_title_when_metadata_title_is_empty():
    item = _raw_item(title="", text="body content here")
    normalized = normalize_raw_item(item)
    assert normalized.title == ""


def test_normalize_does_not_fall_back_to_source_url():
    item = _raw_item(title=None, url="https://x.com/alice/status/1", text="body")
    normalized = normalize_raw_item(item)
    assert normalized.title != "https://x.com/alice/status/1"
    assert normalized.title == ""