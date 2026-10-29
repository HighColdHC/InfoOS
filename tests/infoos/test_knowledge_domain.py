"""Knowledge field constraints, triage and channel semantics."""
from copy import deepcopy

import pytest

from backend.domain.intelligence.knowledge import deterministic_channel, freshness, validate_knowledge
from backend.domain.intelligence.knowledge_kinds import KINDS
from backend.domain.intelligence.triage import validate_triage
from tests.infoos.test_agent_evaluations import item

PROFILE = {"sections": {"主线与KR": ["主线"], "进行中项目": ["InfoOS"], "正在追问的问题": ["问题"],
                        "长期领域": ["领域"], "暂放方向": ["游戏"]}}
TEXT = "原文证据：有人付费购买。"


def knowledge(kind="project", **changes):
    fields = {}
    for key, rule in KINDS[kind].fields.items():
        if "properties" in rule:
            fields[key] = {child: None for child in rule["properties"]}
        elif "max_items" in rule:
            fields[key] = [rule["enum"][0] if "enum" in rule else "动作"] * rule["min_items"]
        elif rule.get("type") == "integer":
            fields[key] = 80
        else:
            fields[key] = None if rule["nullable"] else "说明"
    return {"action": "new", "kind": kind, "subtype": next(iter(KINDS[kind].subtypes)), "title": "案例",
            "attribution_kind": "project", "attribution_ref": "InfoOS", "use_note": "用于项目", "strength": 80,
            "quotes": [TEXT], "fields": fields, **changes}


def validate(value, **kwargs):
    return validate_knowledge(value, PROFILE, is_fresh=True, texts=[TEXT], target=None, **kwargs)


@pytest.mark.parametrize("kind", [key for key, value in KINDS.items() if value.enabled])
def test_each_kind_fields_required_length_enum_and_subtype(kind):
    value = knowledge(kind)
    assert validate(value)["kind"] == kind
    for key, rule in KINDS[kind].fields.items():
        if not rule.get("nullable"):
            broken = deepcopy(value)
            broken["fields"].pop(key)
            with pytest.raises(ValueError, match=f"invalid_{key}"):
                validate(broken)
        if "max_length" in rule:
            with pytest.raises(ValueError, match=f"invalid_{key}"):
                validate({**value, "fields": {**value["fields"], key: "长" * (rule["max_length"] + 1)}})
        elif "max_items" in rule:
            for invalid in (["条"] * (rule["max_items"] + 1), ["长" * (rule["item_max_length"] + 1)]):
                with pytest.raises(ValueError, match=f"invalid_{key}"):
                    validate({**value, "fields": {**value["fields"], key: invalid}})
            if "enum" in rule:
                with pytest.raises(ValueError, match=f"invalid_{key}"):
                    validate({**value, "fields": {**value["fields"], key: ["not_an_enum"]}})
        elif rule.get("type") == "integer":
            with pytest.raises(ValueError, match=f"invalid_{key}"):
                validate({**value, "fields": {**value["fields"], key: True}})
    with pytest.raises(ValueError, match="invalid_subtype"):
        validate({**value, "subtype": "bad"})


def test_attribution_quotes_disabled_and_borrowable():
    result = validate(knowledge(attribution_ref=" InfoOS"))
    assert (result["attribution_kind"], result["attribution_ref"], result["use_note"]) == ("none", None, "暂无直接用处")
    assert validate(knowledge(quotes=["原文证据： 有人付费购买。 "]))["quotes"]
    with pytest.raises(ValueError, match="quote_not_in_card"):
        validate(knowledge(quotes=["编造的证据"]))
    for kind in ("product", "resource"):
        with pytest.raises(ValueError, match="kind_disabled"):
            validate(knowledge(kind))
    with pytest.raises(ValueError, match="borrow_point_required"):
        validate(knowledge("topic", subtype="borrowable"))
    value = knowledge("topic", subtype="borrowable")
    value["fields"]["borrow_point"] = "结构"
    assert validate(value)["fields"]["borrow_point"] == "结构"


@pytest.mark.parametrize("ref,expected", [("主线", "kr"), ("InfoOS", "project"), ("问题", "question"), ("领域", "domain")])
def test_attribution_kind_follows_profile_section(ref, expected):
    result = validate(knowledge(attribution_kind="domain" if expected != "domain" else "project", attribution_ref=ref))
    assert (result["attribution_kind"], result["attribution_ref"], result["use_note"]) == (expected, ref, "用于项目")


def test_paused_attribution_and_explicit_none_are_downgraded():
    for changes in ({"attribution_ref": "游戏"}, {"attribution_kind": "none", "attribution_ref": "InfoOS"}):
        result = validate(knowledge(**changes))
        assert (result["attribution_kind"], result["attribution_ref"], result["use_note"]) == ("none", None, "暂无直接用处")


@pytest.mark.parametrize("published,entered,at,expected", [
    ("2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z", "2026-10-04T00:00:00Z", "fresh"),
    ("2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z", "2026-10-04T00:00:00.000001Z", "recent"),
    ("2026-10-01T00:00:00Z", "2026-10-31T00:00:00Z", "2026-10-31T00:00:00Z", "recent"),
    ("2026-10-01T00:00:00Z", "2026-10-31T00:00:00Z", "2026-10-31T00:00:01Z", "stock"),
    (None, "2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z", "fresh"),
])
def test_freshness(published, entered, at, expected):
    assert freshness(published, entered, at) == expected


@pytest.mark.parametrize("platform,source_type,payload,expected", [
    ("zsxq", "opportunity", {}, "wind_vane"),
    ("zsxq", "topics", {"raw_metadata": {"tags": ["AI 风向标"]}}, "wind_vane"),
    ("zsxq", "topics", {"is_digest": True}, "digest"),
    ("zsxq", "topics", {"raw_payload": {"is_digest": True}}, "digest"),
    ("zsxq", "topics", {}, None),
    ("rss", "opportunity", {"is_digest": True}, None),
])
def test_deterministic_channel(platform, source_type, payload, expected):
    assert deterministic_channel({"source_platform": platform, "raw_payload": payload}, source_type) == expected


@pytest.mark.parametrize("changes,reason", [
    ({"quality": 59, "exclusion": None}, "low_quality_exclusion_required"),
    ({"attribution_kind": "paused", "exclusion": None}, "paused_exclusion_required"),
    ({"exclusion": "ad", "extract_kinds": ["opportunity"]}, "excluded_extract_kinds"),
    ({"extract_kinds": ["unknown"]}, "invalid_extract_kinds"),
    ({"extract_kinds": ["news"]}, "news_not_fresh"),
])
def test_triage(changes, reason):
    with pytest.raises(ValueError, match=reason):
        validate_triage(item(**changes), is_fresh=False)
    assert validate_triage(item(extract_kinds=["product", "resource"]), is_fresh=False)["extract_kinds"] == ["product", "resource"]


def test_triage_exclusion_priority():
    assert validate_triage(item(quality=59, attribution_kind="paused", exclusion="low_quality"), is_fresh=False)["exclusion"] == "low_quality"
    with pytest.raises(ValueError, match="low_quality_exclusion_required"):
        validate_triage(item(quality=59, attribution_kind="paused", exclusion="entertainment"), is_fresh=False)
    assert validate_triage(item(quality=60, attribution_kind="paused", exclusion="paused"), is_fresh=False)["exclusion"] == "paused"
    with pytest.raises(ValueError, match="paused_exclusion_required"):
        validate_triage(item(quality=60, attribution_kind="paused", exclusion="low_quality"), is_fresh=False)
