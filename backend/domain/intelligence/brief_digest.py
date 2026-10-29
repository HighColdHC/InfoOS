"""Plain-text digest of a frozen daily brief."""
from collections import defaultdict
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def feishu_link(url: str) -> str:
    parts = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parts.query) if key != "from"]
    return urlunsplit(parts._replace(query=urlencode([*query, ("from", "feishu")])))


def format_brief_digest(brief: dict[str, Any], brief_url: str) -> tuple[str, str]:
    if brief.get("layout") == "knowledge_v1":
        return _knowledge_digest(brief, brief_url)
    counts = brief["counts"]
    title = f"InfoOS 日报 {brief['brief_date'][5:]}｜相关 {counts['related']} · 新方向 {counts['exploration']}"
    sections: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in brief["items"]:
        heading = {"exploration": "新方向", "landmark": "格局变化"}.get(item["group"])
        sections[heading if heading is not None else (item["attribution_ref"] or "相关条目")].append(item)
    lines = []
    if not brief["items"]:
        lines.append("今日没有过线的相关条目")
    for heading, items in sections.items():
        lines.append(heading)
        for item in items:
            summary = " ".join(item["summary"].split())
            lines.extend([f"{item['number']}. {item['title']}（{item['platform']}）",
                          summary[:60] + ("…" if len(summary) > 60 else ""),
                          feishu_link(item["deep_link"])])
        lines.append("")
    weekly = brief["weekly_paused"]
    if weekly is not None:
        top = weekly["top"]
        lines.append(f"本周暂放 {weekly['count']} 条" + (f"：{top['title']} {feishu_link(top['deep_link'])}" if top else ""))
    lines.append(f"日报全文：{feishu_link(brief_url)}")
    return title, "\n".join(lines)


def _knowledge_digest(brief: dict[str, Any], brief_url: str) -> tuple[str, str]:
    from backend.domain.intelligence.daily_brief import KNOWLEDGE_SECTIONS, stock_line
    counts = brief["counts"]
    title = (f"InfoOS 日报 {brief['brief_date'][5:]}｜商机 {counts['opportunity']} · "
             f"项目 {counts['project']} · 选题 {counts['topic']}")
    lines = []
    for section, label in KNOWLEDGE_SECTIONS.items():
        lines.append(f"{label}（新增 {counts[section]}）")
        for item in brief["items"]:
            if item["section"] != section:
                continue
            note = " ".join(item["use_note"].split())
            lines.extend([f"{item['number']}. {item['title']}" + ("（更新）" if item["is_update"] else ""),
                          note[:60], feishu_link(item["deep_link"])])
        lines.append("")
    lines.extend([stock_line(brief["stock"]), f"日报全文：{feishu_link(brief_url)}"])
    return title, "\n".join(lines)
