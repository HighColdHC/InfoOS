"""Versioned opportunity candidate rules and external-agent breakdown contract."""
from hashlib import sha256
import json
from typing import Any, Literal

CandidateKind = Literal["prefilter", "wind_vane", "case"]
OPPORTUNITY_CANDIDATE_RULES_VERSION = "opportunity_candidate_v3"
OPPORTUNITY_LENS_VERSION = "opportunity_v1"
ENUM_MEANINGS = {
    "monetization_model": {
        "course": "课程/培训", "community": "付费社群", "ecommerce": "电商/带货",
        "service": "服务/代运营", "consulting": "咨询", "traffic_ads": "流量/广告",
        "agency_franchise": "代理/加盟", "tool_product": "工具/产品", "other": "其他", "unclear": "不明确",
    },
    "risk_flags": {"exaggerated_income": "夸大收益", "pyramid_like": "拉人头", "gray_area": "灰色/违规",
                   "unverifiable": "无法核实", "high_upfront_cost": "前期投入高", "platform_risk": "平台风控"},
    "next_step": {"verify": "值得接触核实", "observe": "持续观察", "skip": "放弃"},
}
FIELD_CONSTRAINTS = {
    "monetization_model": {"enum": list(ENUM_MEANINGS["monetization_model"])},
    "offer": {"min_length": 1, "max_length": 300},
    "price_signal": {"nullable": True, "max_length": 200},
    "funnel": {"min_length": 1, "max_length": 500},
    "target_customer": {"nullable": True, "max_length": 200},
    "evidence": {"min_items": 1, "max_items": 10, "item_max_length": 200},
    "replicability": {"type": "integer", "minimum": 0, "maximum": 100},
    "credibility": {"type": "integer", "minimum": 0, "maximum": 100},
    "risk_flags": {"min_items": 0, "max_items": 6, "enum": list(ENUM_MEANINGS["risk_flags"])},
    "next_step": {"enum": list(ENUM_MEANINGS["next_step"])},
    "verify_questions": {"min_items": 0, "max_items": 5, "item_max_length": 200,
                         "verify_min_items": 1},
    "breakdown_note": {"min_length": 1, "max_length": 800, "language": "中文"},
}
OPPORTUNITY_LENS_TEXT = (
    "商机拆解与 rubric_v1 通用评估并列。抖音初筛 matched/unknown 仅代表线索，"
    "知识星球 opportunity 来源仅代表风向标，不证明收益或成交。"
    "只依据卡内具体文字或 block 类型，说明卖什么、价格线索、从内容到成交的路径、目标客户，"
    "区分事实、推断与未知，不得编造证据。结合用户关注画像判断可复制性（0–100），"
    "按卡内证据的具体性与可核实程度判断可信度（0–100），识别风险并给出中文拆解。"
    "需要用户亲自接触核实时列出具体问题；信息不足时 monetization_model='unclear'、"
    "next_step='observe'。不保存联系方式或聊天全文，不代用户入群、私信、评论或点赞。"
    "candidate_kind='case' 时按案例口径：offer 写案例里的生意，即谁卖什么给谁、怎么收钱。"
    "verify 表示值得用户亲自复刻或向作者求证，须同时满足：①与当前主线或进行中项目有具体关联；"
    "②卡内的产品或服务形态、获客渠道、交付方式至少两项具体；"
    "③有收入、价格、单量、转化等具体数字，或作者可在原平台接触（如星球成员可评论或私信）。"
    "verify_questions 写复刻前要先弄清的问题；不满足时 observe 或 skip。"
    "账号/要约去重沿用现有规则，案例卡只与案例卡去重。"
)
WORKFLOW = (
    "定时 agent 完成通用评估后，先读 get_profile 与 get_opportunity_lens，再用 list_opportunity_candidates "
    "读取未拆解卡，用 get_cards 读取卡内证据；truncated 且证据不足时用 get_card，"
    "最后 submit_opportunity_breakdowns 分批写回（1–20条），每条携带已读取的 profile_version。只依据卡内证据，"
    "信息不足时 monetization_model='unclear'、next_step='observe'；"
    "list_opportunities 查看已拆解商机，给用户附深链接，接触核实由用户完成。"
)


def prefilter_evidence(raw_payload: Any) -> dict[str, Any]:
    if not isinstance(raw_payload, dict):
        return {}
    if "prefilter" in raw_payload:
        value = raw_payload["prefilter"]
    else:
        metadata = raw_payload.get("raw_metadata")
        value = metadata.get("prefilter") if isinstance(metadata, dict) else None
    return value if isinstance(value, dict) else {}


def has_wind_vane_tag(tags: Any) -> bool:
    """Variants such as "AI 风向标" count too (user decision 2026-10-27)."""
    return isinstance(tags, list) and any(isinstance(tag, str) and "风向标" in tag for tag in tags)


def is_opportunity_candidate(raw_payload: Any, platform: str | None, source_type: str | None) -> bool:
    status = prefilter_evidence(raw_payload).get("status")
    metadata = raw_payload.get("raw_metadata") if isinstance(raw_payload, dict) else None
    tagged = has_wind_vane_tag(metadata.get("tags") if isinstance(metadata, dict) else None)
    return status in ("matched", "unknown") or (platform == "zsxq" and (source_type == "opportunity" or tagged))


def validate_breakdown(value: Any) -> dict[str, Any]:
    """Return trimmed fields or raise ValueError with a field-specific error code."""
    if not isinstance(value, dict):
        raise ValueError("opportunity_not_object")
    result = {}
    for field, rule in FIELD_CONSTRAINTS.items():
        item = value.get(field)
        if rule.get("nullable") and item is None:
            result[field] = None
            continue
        if "max_items" in rule:
            if not isinstance(item, list) or not rule["min_items"] <= len(item) <= rule["max_items"]:
                raise ValueError(f"opportunity_invalid_{field}")
            normalized = []
            for part in item:
                if not isinstance(part, str) or not part.strip():
                    raise ValueError(f"opportunity_invalid_{field}")
                part = part.strip()
                if "enum" in rule and part not in rule["enum"]:
                    raise ValueError(f"opportunity_invalid_{field}")
                if len(part) > rule.get("item_max_length", 200):
                    raise ValueError(f"opportunity_{field}_too_long")
                normalized.append(part)
            result[field] = normalized
        elif rule.get("type") == "integer":
            if type(item) is not int or not 0 <= item <= 100:
                raise ValueError(f"opportunity_invalid_{field}")
            result[field] = item
        else:
            if not isinstance(item, str):
                raise ValueError(f"opportunity_invalid_{field}")
            item = item.strip()
            if len(item) < rule.get("min_length", 0) or ("enum" in rule and item not in rule["enum"]):
                raise ValueError(f"opportunity_invalid_{field}")
            if len(item) > rule.get("max_length", 800):
                raise ValueError(f"opportunity_{field}_too_long")
            result[field] = item
    if result["next_step"] == "verify" and not result["verify_questions"]:
        raise ValueError("opportunity_verify_questions_required")
    if not any("\u3400" <= char <= "\u9fff" for char in result["breakdown_note"]):
        raise ValueError("opportunity_breakdown_note_requires_chinese")
    return result


def format_breakdown(value: dict[str, Any]) -> str:
    def lines(field):
        return "\n".join(f"- {text}" for text in value[field]) or "无"
    risks = "、".join(ENUM_MEANINGS["risk_flags"][flag] for flag in value["risk_flags"]) or "未标记"
    return (
        f"**变现模式**：{ENUM_MEANINGS['monetization_model'][value['monetization_model']]}\n\n"
        f"**卖什么**：{value['offer']}\n\n**价格线索**：{value['price_signal'] or '未知'}\n\n"
        f"**引流与成交路径**：{value['funnel']}\n\n**目标客户**：{value['target_customer'] or '未知'}\n\n"
        f"**卡内证据**\n{lines('evidence')}\n\n"
        f"**可复制性**：{value['replicability']}/100；**可信度**：{value['credibility']}/100\n\n"
        f"**风险**：{risks}\n\n**下一步建议**：{ENUM_MEANINGS['next_step'][value['next_step']]}\n\n"
        f"**核实问题**\n{lines('verify_questions')}\n\n**拆解**\n{value['breakdown_note']}"
    )


BREAKDOWN_SNAPSHOT_FIELDS = (
    "breakdown_id", "card_id", "card_version", "profile_version", "raw_item_revision", "lens_version", "rules_version",
    "evaluator", "token_id", *FIELD_CONSTRAINTS, "created_at",
)


def breakdown_snapshot(value: dict[str, Any]) -> dict[str, Any]:
    """Hash only explicit stored fields, never read-time profile status."""
    snapshot = {field: value[field] for field in BREAKDOWN_SNAPSHOT_FIELDS}
    encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {**snapshot, "breakdown_content_hash": sha256(encoded.encode()).hexdigest()}
