"""Verification field bounds and contact-information rejection without I/O."""
from uuid import uuid4

import pytest

from backend.domain.intelligence.verification import (
    FIELDS, REQUIRED, TEXT_LIMITS, VerificationError, contains_contact_info, validate_verification,
)


def item(**changes):
    return {"client_verification_id": uuid4().hex, "card_id": "card-1", "recorder": "claude-code:opus", "contact_channel": "wechat",
            "offer": "四周课程", "credibility": "unclear", "conclusion": "keep_observing", **changes}


@pytest.mark.parametrize("text", [
    "13812345678", "电话 138 1234 5678", "138-1234-5678", "+86 138 - 1234 - 5678",
    "1 3 8 1 2 3 4 5 6 7 8", "邮箱 someone+tag@example.com", "微信:abc_123", "微信：abc123",
    "vx abc123", "WX:abc123", "v信 abc123", "薇：abc123", "威信 abc123", "QQ12345", "qq:12345678901", "扣扣 123456",
])
def test_contact_detection(text):
    assert contains_contact_info(text)
    with pytest.raises(VerificationError) as error:
        validate_verification(item(note=text))
    assert error.value.as_dict() == {"error": "contact_info_not_allowed",
        "message": "核实记录不得包含联系方式，请删除后重试", "field": "note"}


@pytest.mark.parametrize("field", [f for f in FIELDS if f != "replicability"])
def test_all_text_fields_screened(field):
    with pytest.raises(VerificationError) as error:
        validate_verification(item(**{field: "someone@example.com"}))
    assert error.value.code == "contact_info_not_allowed" and error.value.field == field


@pytest.mark.parametrize("text", ["2999 元", "2026-10-14", "123456", "123456789012", "微信咨询后购买", "价格 138 元", "微信：课程价格 2999 元"])
def test_non_contacts_allowed(text):
    assert not contains_contact_info(text)
    assert validate_verification(item(note=text))["note"] == text


@pytest.mark.parametrize("field", sorted(REQUIRED))
@pytest.mark.parametrize("value", [None, "", "   ", 3, []])
def test_required_fields(field, value):
    with pytest.raises(VerificationError) as error:
        validate_verification(item(**{field: value}))
    assert error.value.field == field


@pytest.mark.parametrize("field,limit", TEXT_LIMITS.items())
def test_text_bounds(field, limit):
    assert validate_verification(item(**{field: "文" * limit}))[field] == "文" * limit
    with pytest.raises(VerificationError):
        validate_verification(item(**{field: "文" * (limit + 1)}))


@pytest.mark.parametrize("changes", [
    {"contact_channel": "email"}, {"credibility": "good"}, {"conclusion": "action"},
    {"replicability": True}, {"replicability": -1}, {"replicability": 101}, {"replicability": 1.5}, {"replicability": "10"},
    {"contacted_on": "2026-02-29"}, {"contacted_on": "20261014"}, {"contacted_on": "2026-1-1"},
    {"contacted_on": "2026-10-14T00:00:00Z"}, {"contacted_on": ""}, {"supersedes": " "}, {"note": {}},
])
def test_invalid_fields(changes):
    with pytest.raises(VerificationError):
        validate_verification(item(**changes))


def test_valid_optional_fields():
    result = validate_verification(item(recorder=" agent:test ", contacted_on="2024-02-29", replicability=0))
    assert result["recorder"] == "agent:test" and result["price"] is None
    assert result["replicability"] == 0
    assert validate_verification(item(replicability=100))["replicability"] == 100
