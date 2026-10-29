"""Single source of truth for external-agent evaluation."""
from backend.domain.intelligence.triage import TRIAGE_TEXT, TRIAGE_VERSION

RUBRIC_VERSION = TRIAGE_VERSION
QUALITY_THRESHOLD = 60
EXPLORATION_QUALITY_THRESHOLD = 80
EXPLORATION_CONTENT_TYPES = ("case", "method", "tool", "data")
ENUM_MEANINGS = {
    "attribution_kind": {"project": "进行中项目", "kr": "主线与KR", "question": "正在追问的问题",
                         "domain": "长期领域", "exploration": "与画像无关的新方向", "paused": "暂放方向"},
    "content_type": {"event": "事件/新闻", "method": "方法/教程", "case": "案例/拆解",
                     "opinion": "观点/评论", "tool": "工具/产品", "data": "数据/报告"},
    "suggested_action": {"aware": "知悉", "read": "阅读全文", "distill_candidate": "候选知识沉淀",
                         "action_candidate": "候选行动，须用户确认"},
}
RUBRIC_TEXT = """按当前关注画像评估每张信息卡，返回以下字段：
quality：0–100 整数，衡量信息密度、一手性、可信度；广告、水文、纯转发、无信息量给低分。
relevance：0–100 整数，对照画像中的主线与KR、进行中项目、正在追问的问题、长期领域；暂放方向为 null。
attribution_kind：project（进行中项目）、kr（主线与KR）、question（正在追问的问题）、domain（长期领域）、exploration（与画像无关的新方向）、paused（暂放方向）。
attribution_ref：必须原样引用画像中的一个列表条目；找不到时会降级归属；exploration 可为空。与画像无关但质量高的内容归为 exploration。
命中暂放方向必须用 paused、relevance=null。paused_landmark 为布尔值，仅平台或行业规则剧变、或直接改变暂放判断的新证据等格局级变化才为 true；普通内容为 false。
content_type：event（事件/新闻）、method（方法/教程）、case（案例/拆解）、opinion（观点/评论）、tool（工具/产品）、data（数据/报告）。
suggested_action：aware（知悉）、read（阅读全文）、distill_candidate（候选知识沉淀）、action_candidate（候选行动，须用户确认）。以上只是 agent 建议。
summary：一两句中文摘要。relation_note：一句说明和哪个项目或问题有关、可以怎么用；exploration 说明为何值得看，paused 说明触及哪个暂放方向。
质量阈值为 60，未通过的评估仍保存，但不生成评估 block。
先调用 get_evaluation_rubric 和 get_profile，再用 list_new_cards 取未评估卡，get_cards 每批最多20张读取，submit_evaluations 每批最多20条写回。给用户看的内容须附卡片深链接。
"""

RUBRIC_TEXT += TRIAGE_TEXT
