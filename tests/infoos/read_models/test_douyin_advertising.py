import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.application.setup.state import AccountStatus
from backend.application.tasks.handlers.acquisition import handle_card_build
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskType
from backend.application.usecases.api_tokens import create_token
from backend.application.workflows.acquisition import fetch_source
from backend.domain.advertising import AD_FIELDS, advertising_projection
from backend.domain.sources.configuration import validate_source_payload
from backend.infrastructure.storage.repositories import CardRepository, SourceRepository
from backend.read_models.card_preview import read_card_preview_detail
from backend.read_models.new_cards import list_new_cards
from backend.read_models.plugin_cards import read_plugin_card_detail, read_plugin_card_page
from tests.infoos.test_mcp_interface import _call


def test_projection_normalizes_only_ads():
    assert advertising_projection({"is_ad": False, "ad_id": "1"}) == {}
    assert advertising_projection("invalid") == {}
    ad = advertising_projection({"raw_payload": {"opencli_record": {
        "is_ad": "true", "ad_id": "12345678901234567890", "ad_is_soft": "0", "ad_group_type": "invalid",
    }}})["ad"]
    assert set(ad) == {"is_ad", *AD_FIELDS}
    assert ad["ad_id"] == "12345678901234567890"
    assert ad["ad_is_soft"] is False
    assert ad["ad_group_type"] == 0
    assert ad["ad_title"] == ""


def test_acquisition_to_cards_and_mcp_exposes_ad_without_extra_tools(tmp_path, monkeypatch):
    source = validate_source_payload({
        "source_id": "dy_ads", "platform": "douyin", "source_type": "home_feed",
        "identifier": "recommended", "cadence_hours": 1, "enabled": True,
    })
    SourceRepository(tmp_path).upsert_source({**source, "connector_status": "supported"})
    payload = [{"aweme_id": str(number), "url": f"https://www.douyin.com/video/{number}",
                "title": "广告视频" if number == 1 else "普通视频", "is_live": False, "is_ad": number == 1,
                "ad_id": "12345678901234567890" if number == 1 else "", "ad_title": "广告标题",
                "ad_kind": "aweme_dou_plus", "ad_is_soft": True} for number in (1, 2)]
    payload.append({"is_live": True, "url": "", "title": "直播"})
    monkeypatch.setattr("backend.application.setup.verify.probe_platform",
                        lambda platform: SimpleNamespace(status=AccountStatus.VERIFIED))
    monkeypatch.setattr("backend.infrastructure.connectors.opencli.subprocess.run",
                        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr=""))
    monkeypatch.setattr("backend.application.workflows.acquisition.utc_now_seconds_z", lambda: "2026-10-09T10:30:00Z")

    class FixedRandom:
        def randint(self, minimum, maximum):
            return minimum

    monkeypatch.setattr("backend.application.workflows.acquisition.Random", FixedRandom)
    result = fetch_source(tmp_path, source_id="dy_ads", task=Task("dy-fetch", TaskType.FETCH, "source", "dy_ads"))
    assert result["raw_item_count"] == 2
    assert result["dedup_skipped_count"] == 1
    assert SourceRepository(tmp_path).get_source("dy_ads")["next_run_at"] == "2026-10-10T01:00:00Z"
    cards = []
    for task_id in result["card_build_task_ids"]:
        task = TaskRepository.for_base_dir(tmp_path).get(task_id)
        built = handle_card_build(task, tmp_path)
        card = CardRepository(tmp_path).get_card(built["artifacts"]["card_id"])
        CardRepository(tmp_path).finalize_card(card["card_id"], content_hash="sha256:" + "a" * 64,
                                                completeness_status="partial", missing_reasons=[])
        cards.append(card)
    ad_id, plain_id = [card["card_id"] for card in cards]
    expected = advertising_projection(cards[0]["raw_payload"])["ad"]
    for reader in (read_plugin_card_detail, read_card_preview_detail):
        assert reader(tmp_path, ad_id)["card"]["ad"] == expected
        assert "ad" not in reader(tmp_path, plain_id)["card"]
    assert {item["card_id"]: item.get("ad") for item in read_plugin_card_page(tmp_path)["items"]} == {
        ad_id: expected, plain_id: None,
    }
    new = list_new_cards(tmp_path, "2020-01-01T00:00:00Z")["items"]
    assert len(new) == 2
    assert new[0]["ad"] == expected
    assert "ad" not in new[1]
    assert all("raw_payload" not in item for item in new)
    token = create_token(tmp_path, name="ad-reader", scopes=["intelligence:read"])["plaintext_token"]
    with TestClient(create_app(tmp_path)) as client:
        for name, arguments in (("get_card", {"card_id": ad_id}), ("get_cards", {"card_ids": [ad_id]}),
                                ("list_new_cards", {"since": "2020-01-01T00:00:00Z"})):
            response = _call(client, "tools/call", {"name": name, "arguments": arguments}, token)
            data = response.json()["result"]["structuredContent"]
            item = data if name == "get_card" else data["items"][0]
            assert item["ad"] == expected
