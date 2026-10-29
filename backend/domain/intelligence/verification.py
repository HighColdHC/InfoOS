"""Pure validation and Chinese rendering for user-confirmed verification records."""
from datetime import date
from hashlib import sha256
import json
import re
from typing import Any

from backend.domain.intelligence.opportunity import ENUM_MEANINGS

VERIFICATION_SCHEMA_VERSION = "verification_v1"
CONTACT_CHANNELS = {"wechat": "微信", "phone": "电话", "platform_dm": "平台私信", "in_person": "当面", "other": "其他"}
CREDIBILITY = {"credible": "可信", "doubtful": "存疑", "scam_suspected": "疑似骗局", "unclear": "尚不明确"}
CONCLUSIONS = {"keep_observing": "继续观察", "drop": "放弃", "to_action": "转入 PKM 行动"}
TEXT_LIMITS = {"recorder": 200, "offer": 500, "price": 300, "delivery": 500,
               "funnel": 500, "doubts": 800, "note": 1500}
REQUIRED = {"card_id", "recorder", "contact_channel", "offer", "credibility", "conclusion"}
FIELDS = ("card_id", "recorder", "contact_channel", "offer", "credibility", "conclusion",
          "contacted_on", "price", "delivery", "funnel", "doubts", "replicability", "note", "supersedes")
_CONTACT_PATTERNS = (
    re.compile(r"(?<!\d)1[\s-]*[3-9](?:[\s-]*\d){9}(?!\d)"),
    re.compile(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+", re.I),
    re.compile(r"(?:微信|vx|wx|v信|薇|威信)(?:\s*[:：]\s*|\s+)[a-z0-9_][a-z0-9_-]*", re.I),
    re.compile(r"(?:qq|扣扣)\s*[:：]?\s*\d{5,11}(?!\d)", re.I),
)


class VerificationError(ValueError):
    def __init__(self, code: str, message: str, field: str | None = None):
        super().__init__(code)
        self.code, self.message, self.field = code, message, field

    def as_dict(self):
        return {"error": self.code, "message": self.message, **({"field": self.field} if self.field else {})}


def contains_contact_info(text: str) -> bool:
    return any(pattern.search(text) for pattern in _CONTACT_PATTERNS)


def validate_verification(payload: dict[str, Any]) -> dict[str, Any]:
    result = {field: payload.get(field) for field in FIELDS}
    for field, value in result.items():
        if isinstance(value, str) and contains_contact_info(value):
            raise VerificationError("contact_info_not_allowed", "核实记录不得包含联系方式，请删除后重试", field)
        if field == "replicability":
            if value is not None and (type(value) is not int or not 0 <= value <= 100):
                raise VerificationError("invalid_field", "可复制性必须为 0–100 整数或 null", field)
            continue
        if value is None and field not in REQUIRED:
            continue
        if not isinstance(value, str):
            raise VerificationError("invalid_field", "字段必须为文字，必填项不可为空", field)
        result[field] = value = value.strip()
        if field in REQUIRED | {"supersedes", "contacted_on"} and not value:
            raise VerificationError("invalid_field", "字段不可为空", field)
        if field in TEXT_LIMITS and len(value) > TEXT_LIMITS[field]:
            raise VerificationError("invalid_field", f"字段最多 {TEXT_LIMITS[field]} 字符", field)
    for field, choices in (("contact_channel", CONTACT_CHANNELS), ("credibility", CREDIBILITY), ("conclusion", CONCLUSIONS)):
        if result[field] not in choices:
            raise VerificationError("invalid_field", "字段不在允许的枚举范围", field)
    if result["contacted_on"] is not None:
        try:
            value = result["contacted_on"]
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
                raise ValueError()
            date.fromisoformat(value)
        except ValueError:
            raise VerificationError("invalid_field", "接触日期必须为有效的 YYYY-MM-DD", "contacted_on") from None
    client_id = payload.get("client_verification_id")
    if not isinstance(client_id, str) or not 1 <= len(client_id.strip()) <= 128:
        raise VerificationError("invalid_field", "client_verification_id 必须为 1–128 字符", "client_verification_id")
    result["client_verification_id"] = client_id.strip()
    for field in ("breakdown_id", "expected_breakdown_hash"):
        value = payload.get(field)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise VerificationError("invalid_breakdown", "关联拆解 ID 与摘要必须同时提供或同时为空", field)
        result[field] = value.strip() if value is not None else None
    if (result["breakdown_id"] is None) != (result["expected_breakdown_hash"] is None):
        raise VerificationError("invalid_breakdown", "关联拆解 ID 与摘要必须同时提供或同时为空")
    return result


def verification_request_hash(fields: dict[str, Any]) -> str:
    return sha256(json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def format_verification(record: dict[str, Any]) -> str:
    lines = [f"人工核实 · 记录日期 {record['created_at'][:10]}",
             f"接触日期：{record['contacted_on'] or '未知'}", f"记录者：{record['recorder']}",
             f"接触渠道：{CONTACT_CHANNELS[record['contact_channel']]}"]
    for field, label in (("offer", "实际提供"), ("price", "价格"), ("delivery", "交付方式"),
                         ("funnel", "引流与成交路径"), ("doubts", "疑点"), ("replicability", "可复制性"), ("note", "备注")):
        value = record[field]
        lines.append(f"{label}：{value if value is not None and value != '' else '未知'}")
    lines.extend([f"可信度：{CREDIBILITY[record['credibility']]}", f"结论：{CONCLUSIONS[record['conclusion']]}"])
    snapshot = record["breakdown_snapshot"]
    if snapshot is None:
        lines.append("未关联 AI 拆解")
    else:
        lines.extend([
            f"关联 AI 拆解：{snapshot['breakdown_id']}",
            f"当时建议：{ENUM_MEANINGS['next_step'][snapshot['next_step']]}",
            f"当时依据：卡片 v{snapshot['card_version']}；画像 "
            f"{snapshot['profile_version'] if snapshot['profile_version'] is not None else '未知'}；"
            f"视角 {snapshot['lens_version']}；规则 {snapshot['rules_version']}；原料修订 {snapshot['raw_item_revision']}",
            f"当时拆解：{snapshot['breakdown_note']}",
        ])
    if record["supersedes"]:
        lines.append(f"更正记录：{record['supersedes']}（原记录保留）")
    return "\n\n".join(lines)
