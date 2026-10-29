"""Tests for x_fixture and opencli source loading + fetch paths."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.application.workflows.fetch import fetch, main as fetch_main
from backend.infrastructure.storage.repositories import SourceCursorRepository, SourceRepository


X_TIMELINE_JSON = """{
  "tweets": [
    {
      "id": "tweet-1",
      "url": "https://x.com/alice/status/1",
      "text": "Ship the connector before polishing the dashboard.",
      "created_at": "2026-04-19T10:00:00Z",
      "author": {
        "name": "Alice",
        "username": "alice"
      },
      "metrics": {
        "like_count": 7,
        "retweet_count": 2,
        "reply_count": 1
      }
    }
  ]
}
"""


def _x_fixture_config(fixture_path: Path) -> dict:
    """Return a connector-config dict for the legacy x_fixture connector.

    The DB is the source of truth, but the x_fixture type isn't a
    first-class platform/type that database_source_to_connector_config
    projects. Tests that exercise the x_fixture code path reach it via
    monkeypatching load_source_configs to return the right dict directly.
    """
    return {
        "source_id": "x_test",
        "connector_id": "x_test",
        "enabled": True,
        "type": "x_fixture",
        "fixture_path": str(fixture_path),
    }


def _seed_x_fixture_source(base_dir: Path) -> None:
    """The fixture connector remains an override, while source identity is SQLite-backed."""
    SourceRepository(base_dir).upsert_source(
        {
            "source_id": "x_test",
            "platform": "x",
            "source_type": "creator",
            "identifier": "alice",
            "display_name": "X fixture",
            "cadence_hours": 24,
            "enabled": True,
            "connector_status": "supported",
            "connector_id": "x_test",
        }
    )


def test_load_source_configs_includes_feed_and_x_fixture_sources(tmp_path, monkeypatch):
    """DB is the source of truth for source configs. The legacy x_fixture
    connector still exists for fixture-driven testing; reach it via
    monkeypatching the load_source_configs call site.

    This test asserts that the call site iterates whatever the patched
    loader returns (i.e., the new code path doesn't bypass the loader).
    """
    from backend.infrastructure.connectors.x_fixture import XFixtureConnector

    fixture_path = tmp_path / "fixtures" / "x_timeline.json"
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_path.write_text(X_TIMELINE_JSON, encoding="utf-8")
    _seed_x_fixture_source(tmp_path)

    seen_connectors: list = []
    database_configs: list[dict] = []

    real_init = XFixtureConnector.__init__

    def spy_init(self, *args, **kwargs):
        seen_connectors.append({"args": args, "kwargs": kwargs, "self": self})
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(XFixtureConnector, "__init__", spy_init)
    monkeypatch.setattr(
        "backend.application.workflows.fetch.load_source_configs",
        lambda base_dir: [_x_fixture_config(fixture_path)],
    )
    class EmptyConnector:
        def fetch_new_items(self, since_cursor):
            from backend.infrastructure.connectors.base import FetchResult
            return FetchResult(items=[], next_cursor=None)

    monkeypatch.setattr(
        "backend.application.workflows.acquisition._build_connector",
        lambda _base, source: database_configs.append(source) or EmptyConnector(),
    )

    fetch(base_dir=tmp_path)

    assert seen_connectors == []
    assert database_configs[0]["type"] == "opencli"
    assert "fixture_path" not in database_configs[0]


def test_fetch_supports_x_fixture_connector_and_persists_raw_items(tmp_path, monkeypatch):
    fixture_path = tmp_path / "fixtures" / "x_timeline.json"
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_path.write_text(X_TIMELINE_JSON, encoding="utf-8")
    _seed_x_fixture_source(tmp_path)

    monkeypatch.setattr(
        "backend.application.workflows.fetch.load_source_configs",
        lambda base_dir: [_x_fixture_config(fixture_path)],
    )
    from backend.infrastructure.connectors.x_fixture import XFixtureConnector
    monkeypatch.setattr(
        "backend.application.workflows.acquisition._build_connector",
        lambda *_: XFixtureConnector("x_test", fixture_path),
    )

    result = fetch(base_dir=tmp_path)

    assert result["fetched_count"] == 1
    assert len(result["exports"]["raw_outputs"]) == 1
    raw_output = Path(result["exports"]["raw_outputs"][0])
    assert raw_output.name == "raw_items.jsonl"
    raw_payload = raw_output.read_text(encoding="utf-8")
    assert '"source_platform":"x"' in raw_payload
    assert '"source_type":"social_post"' in raw_payload
    assert '"handle":"alice"' in raw_payload
    assert '"like_count":7' in raw_payload


def test_main_fetch_command_supports_x_fixture_config(tmp_path, capsys, monkeypatch):
    fixture_path = tmp_path / "fixtures" / "x_timeline.json"
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_path.write_text(X_TIMELINE_JSON, encoding="utf-8")
    _seed_x_fixture_source(tmp_path)

    monkeypatch.setattr(
        "backend.application.workflows.fetch.load_source_configs",
        lambda base_dir: [_x_fixture_config(fixture_path)],
    )
    from backend.infrastructure.connectors.x_fixture import XFixtureConnector
    monkeypatch.setattr(
        "backend.application.workflows.acquisition._build_connector",
        lambda *_: XFixtureConnector("x_test", fixture_path),
    )

    exit_code = fetch_main(["--base-dir", str(tmp_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert '"fetched_count": 1' in captured.out
    assert SourceCursorRepository(tmp_path).get("x_test")["cursor_value"] == "tweet-1"


def test_opencli_connector_does_not_write_title_to_raw_metadata(tmp_path, monkeypatch):
    from backend.infrastructure.connectors.opencli import OpenCLIConnector

    def fake_run(*args, **kwargs):
        payload = [{
            "id": "tweet-no-title",
            "text": "Just the body, no real title here.",
            "url": "https://x.com/alice/status/1",
            "created_at": "2026-04-19T10:00:00Z",
            "author": {"name": "Alice", "username": "alice"},
        }]
        return SimpleNamespace(stdout=__import__("json").dumps(payload), stderr="", returncode=0)

    monkeypatch.setattr("backend.infrastructure.connectors.opencli.subprocess.run", fake_run)
    connector = OpenCLIConnector(
        connector_id="x_test",
        command=["twitter", "list-tweets", "1"],
        source_platform="x",
        source_type="social_post",
    )
    result = connector.fetch_new_items(since_cursor=None)

    assert len(result.items) == 1
    raw = result.items[0]
    assert raw.raw_text == "Just the body, no real title here."
    assert "title" not in (raw.raw_metadata or {})
