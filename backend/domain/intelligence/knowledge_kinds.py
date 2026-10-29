"""Versioned knowledge libraries; adding a kind does not require a new table."""
from dataclasses import asdict, dataclass
from typing import Any

from backend.domain.intelligence.opportunity import ENUM_MEANINGS


def text(label: str, maximum: int, *, nullable: bool = False) -> dict[str, Any]:
    return {"label": label, "min_length": 1, "max_length": maximum, "nullable": nullable}


def texts(label: str, minimum: int, maximum: int, length: int = 200, **extra) -> dict[str, Any]:
    return {"label": label, "min_items": minimum, "max_items": maximum, "item_max_length": length, **extra}


def score(label: str) -> dict[str, Any]:
    return {"label": label, "type": "integer", "minimum": 0, "maximum": 100}


@dataclass(frozen=True)
class KnowledgeKind:
    key: str
    label: str
    subtypes: dict[str, str]
    must_have: str
    exclude: str
    fields: dict[str, dict[str, Any]]
    merge_keys: tuple[str, ...]
    stream: bool
    brief_section: str
    brief_cap: int
    archive_purpose: str
    enabled: bool = True


KINDS = {kind.key: kind for kind in (
    KnowledgeKind("project", "项目", {"media_account": "自媒体账号", "side_business": "小生意/副业", "tool_site": "工具站/产品", "service": "服务交付", "mini_game": "小游戏"},
        "主体（谁）、做了什么（产品或服务）、结果（数字或明确结果）、大致时间。", "只晒收入不讲过程、纯情绪复盘、广告。", {
            "subject": text("主体", 100), "one_liner": text("一句话项目", 200), "track": text("赛道", 50),
            "period": text("时间", 50, nullable=True), "key_numbers": texts("关键数字", 0, 5, 100),
            "key_actions": texts("关键动作", 1, 8), "result": text("结果", 300), "pitfalls": texts("踩过的坑", 0, 5),
            "business_model": {"label": "商业模式", "properties": {key: text(label, 200, nullable=True) for key, label in
                (("customer", "卖给谁"), ("offer", "卖什么"), ("pricing", "怎么收钱/价格"), ("funnel", "获客漏斗"))}},
            "credibility": score("可信度"),
        }, ("subject", "one_liner"), False, "project", 3, "项目案例与结果证据"),
    KnowledgeKind("opportunity", "商机", {"case_business": "复盘中的生意", "running_ads": "在投广告", "selling": "带货/售卖", "wind_vane": "风向标短讯"},
        "具体生意（卖什么、卖给谁），加一项赚钱信号：收入、价格、销量、接单、成交、在投广告。",
        "简介只写商务合作的达人；没有生意结构的纯晒收入。agent 不判 verify，不替用户写调研去向。", {
            "business": text("生意", 200), "target_customer": text("目标客户", 200), "offer": text("要约", 300),
            "pricing": text("收费方式与价格", 200, nullable=True), "channels": texts("获客渠道", 0, 5, 100),
            "delivery": text("交付方式", 200, nullable=True), "money_signal": text("赚钱信号", 200),
            "signal_kinds": texts("信号类型", 1, 6, enum=["revenue", "price", "sales", "orders", "deals", "ads"]),
            "risks": texts("风险", 0, 6, enum=list(ENUM_MEANINGS["risk_flags"])),
        }, ("target_customer", "offer", "pricing"), False, "opportunity", 5, "商机线索与候选"),
    KnowledgeKind("method", "方法", {"growth": "获客增长", "content": "内容制作", "product_dev": "产品开发", "operations": "运营交付", "ai_workflow": "AI 工作流"},
        "要解决的问题、至少3步可执行步骤或明确流程、适用条件、效果证据或作者亲自实践。", "口号和观点；单纯工具操作说明归产品卡或资源卡。", {
            "problem": text("解决什么问题", 200), "conditions": text("适用条件", 300), "steps": texts("步骤", 3, 12),
            "tips": texts("要点和坑", 0, 6), "effect_evidence": text("效果证据", 300),
        }, ("problem", "steps"), False, "deposit", 3, "可复用方法与项目资产"),
    KnowledgeKind("topic", "选题", {"hot": "热点", "borrowable": "可借鉴", "series": "系列选题"},
        "热点须72小时内且有角度和播放/点赞/讨论量；可借鉴须数据表现、可借用点、用户怎么做。短内容也可收；与其他类型不互斥。", "没有热度或数据表现证据的泛观点。", {
            "angle": text("选题角度", 200), "heat_evidence": text("热度或数据表现", 200),
            "borrow_point": text("可借用的点", 200, nullable=True), "how_to": text("用户可以怎么做", 300),
        }, ("angle",), False, "topic", 5, "内容选题"),
    KnowledgeKind("news", "资讯", {"ai": "AI 资讯", "platform_rule": "平台规则", "landscape": "行业格局", "policy": "政策"},
        "卡片新鲜，且与画像有具体关联：模型、产品、平台规则、行业格局或政策的新变化。", "测评水文、旧闻、与用户方向关系不大的榜单。", {
            "what_changed": text("发生了什么变化", 300), "why_it_matters": text("为何有关", 300),
        }, ("what_changed",), True, "news", 5, "只做引用；多条资讯可在 PKM 派生事件卡"),
    KnowledgeKind("product", "产品", {"tool": "工具", "reference": "参考产品"},
        "名称、解决什么问题、怎么用或怎么收费；首次出现或大版本更新才算新增。", "商单软广、没有新信息的老工具。", {}, ("name",), False, "deposit", 3, "工具资料", False),
    KnowledgeKind("resource", "资源", {"list": "清单", "prompt_skill": "提示词/Skill", "template": "模板", "course": "教程课程", "report": "数据报告", "code": "开源代码"},
        "可直接获取的链接、文件或清单条目，以及用途；清单保留条目，博主清单只建议新增来源，须用户确认。", "不能直接获取的泛介绍。", {}, ("url",), False, "deposit", 3, "协作与学习资料", False),
)}
COMMON_FIELDS = {"title": text("标题", 80), "use_note": text("用处", 200), "strength": score("证据强度"), "quotes": texts("原文摘句", 1, 5)}


STRENGTH_MEANINGS = {"project": "结果证据强度", "opportunity": "赚钱信号强弱", "method": "效果证据强度",
                      "topic": "热度或数据表现", "news": "影响程度"}


def enabled_schema(pkm_targets: dict[str, str] | None = None) -> list[dict[str, Any]]:
    return [{**asdict(kind), "pkm_target": (pkm_targets or {}).get(kind.key, ""), "common_fields": {**COMMON_FIELDS, "strength": score(STRENGTH_MEANINGS[kind.key]),
        "attribution_kind": {"enum": ["project", "kr", "question", "domain", "none"]},
        "attribution_ref": {"nullable": True, "rule": "逐字引用画像条目，否则降级none；暂无直接用处"}}} for kind in KINDS.values() if kind.enabled]
