"""Lightweight triage contract layered over the existing evaluation fields."""
from typing import Any

from backend.domain.intelligence.knowledge_kinds import KINDS
from backend.domain.intelligence.knowledge import CHANNEL_TEXT, CONFUSIONS

TRIAGE_VERSION = "triage_v1"
EXCLUSIONS = ("low_quality", "ad", "entertainment", "paused")
TRIAGE_TEXT = """
排除原因按唯一优先级选择：质量低于60分一律为 low_quality；质量达到60分及以上时依次判断暂放、广告、娱乐。
分诊判断表：
| 判断 | 标准 | 结果 |
| 质量 | 信息密度、一手性、可信度，60分门槛 | 不过线 exclusion=low_quality，沉底 |
| 广告/商单 | 作者与产品有利益关系且无实测细节，或通篇引流话术 | exclusion=ad，沉底；作为在投广告的生意信号可不排除并标 opportunity |
| 娱乐/无关 | 游戏、八卦、宠物、日常生活且与画像任何条目无关 | exclusion=entertainment，沉底 |
| 暂放方向 | 命中画像暂放方向 | exclusion=paused，沉底；只有格局级变化才提示 |
| 能拆出什么 | 逐个对照类型的必须有标准，可多选 | extract_kinds 进拆解 |
exclusion 必填，可为 null 或 low_quality/ad/entertainment/paused；被排除时 extract_kinds 必须为 []。
extract_kinds 必填数组，取 project/opportunity/method/product/resource/topic/news；一期 product/resource 只标记不拆。
信息类型与知识卡对应：娱乐沉底；工具→产品卡；资源清单→资源卡，博主清单只建议新增来源须用户确认；
项目信息→项目+商机，附带方法、产品，有时选题；实时资讯→资讯（AI、政策等）；
经验教程→方法，附带提示词/模板→资源；观点/讨论默认沉底，数据表现好→可借鉴选题；对标账号爆款→选题+内容制作方法。
资讯只能来自 InfoOS 判定新鲜的卡；确定性渠道也照常分诊，但拆解不受分诊影响。
抖音/小红书推荐流初筛是输入信号，matched 优先看具体生意；不能只按博主或来源固定类型。
"""


def validate_triage(result: dict[str, Any], *, is_fresh: bool) -> dict[str, Any]:
    exclusion = result.get("exclusion")
    if "exclusion" not in result or (exclusion is not None and exclusion not in EXCLUSIONS):
        raise ValueError("invalid_exclusion")
    kinds = result.get("extract_kinds")
    if not isinstance(kinds, list) or any(not isinstance(kind, str) or kind not in KINDS for kind in kinds):
        raise ValueError("invalid_extract_kinds")
    if exclusion is not None and kinds:
        raise ValueError("excluded_extract_kinds")
    if result["quality"] >= 60 and result["attribution_kind"] == "paused" and exclusion != "paused":
        raise ValueError("paused_exclusion_required")
    if result["quality"] < 60 and exclusion != "low_quality":
        raise ValueError("low_quality_exclusion_required")
    if "news" in kinds and not is_fresh:
        raise ValueError("news_not_fresh")
    return {**result, "exclusion": exclusion, "extract_kinds": kinds}

TRIAGE_TEXT += "\n".join(f"{kind.label}：必须有{kind.must_have} 不收：{kind.exclude}" for kind in KINDS.values())
TRIAGE_TEXT += "\n" + CONFUSIONS + "\n" + CHANNEL_TEXT
