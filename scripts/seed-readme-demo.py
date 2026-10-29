#!/usr/bin/env python3
"""Write fictional fixtures using a disposable instance's HTTP API and MCP only."""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx


def seed(base_url: str) -> dict:
    content = json.loads(Path(__file__).with_name("readme-demo-content.json").read_text())
    client = httpx.Client(base_url=base_url, headers={"Origin": base_url}, timeout=45, trust_env=False)

    def api(method, path, **kwargs):
        response = client.request(method, path, **kwargs)
        if response.is_error:
            raise RuntimeError(f"Demo API {path}: {response.status_code} {response.text}")
        response.raise_for_status()
        return response.json()

    token = api("POST", "/api/token-auth/tokens", json={"name": "合成演示专用", "scopes": [
        "captures:write", "intelligence:read", "evaluation:write", "profile:write",
    ]})["plaintext_token"]
    bearer = {"Authorization": f"Bearer {token}"}

    def mcp(name, arguments=None):
        value = api("POST", "/mcp", headers={**bearer, "Accept": "application/json, text/event-stream"},
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": name, "arguments": arguments or {}}})
        if "error" in value or value["result"].get("isError"):
            raise RuntimeError(f"MCP {name} failed")
        result = value["result"]
        return result.get("structuredContent") or json.loads(result["content"][0]["text"])

    profile = """## 主线与KR
- 跑通一个可复查的原型验证流程
## 进行中项目
- 示例开发工具箱
## 正在追问的问题
- 怎样让文档检查结果更容易理解？
## 暂放方向
## 长期领域
- 开源工具与编程方法
"""
    mcp("push_profile", {"markdown": profile})
    mcp("get_profile")
    mcp("get_evaluation_rubric")
    mcp("get_knowledge_schema")
    # Disabled fixtures are never fetched: the demo does not contact these URLs.
    for slug, label in (("tools", "示例开源工具周刊"), ("methods", "示例编程方法博客"), ("docs", "示例文档实践")):
        api("POST", "/api/configurable-sources", json={"platform": "rss", "source_type": "feed",
            "identifier": f"https://example.com/{slug}/feed.xml", "display_name": label,
            "cadence_hours": 24, "max_items_per_run": 10, "enabled": False,
            "notes": "纯合成演示来源；保持停用，不访问网络。"})
    captures = []
    for item in content:
        captures.append(api("POST", "/api/capture/v1/items",
            headers={**bearer, "Idempotency-Key": f"demo-{item['kind']}"}, json={
            "schema": "infoos.capture-request.v1", "client_capture_id": f"demo-{item['kind']}",
            "url": f"https://example.com/articles/{item['kind']}", "title": item["title"],
            "captured_at": datetime.now(ZoneInfo("UTC")).isoformat(), "capture_mode": "readable_content",
            "rendered_content": {"format": "readability_html", "text": item["body"],
                                 "byline": "示例编辑组", "site_name": "示例博客"},
            "client": {"name": "readme-demo", "version": "1"}}))
    card_ids = []
    for capture in captures:
        for _ in range(120):
            receipt = api("GET", f"/api/capture/v1/items/{capture['capture_id']}", headers=bearer)
            if receipt.get("processing_status") == "succeeded" and receipt.get("card_id"):
                card_ids.append(receipt["card_id"])
                break
            if receipt.get("processing_status") == "failed":
                raise RuntimeError("Synthetic capture failed")
            time.sleep(0.5)
        else:
            raise TimeoutError("Synthetic capture did not finalize")
    evaluated = mcp("submit_evaluations", {"evaluator": "demo:synthetic", "items": [{
        "card_id": card_id, "quality": 80, "relevance": 85, "attribution_kind": "project",
        "attribution_ref": "示例开发工具箱", "content_type": "case" if item["kind"] in {"project", "opportunity"} else "method", "suggested_action": "read",
        "exclusion": None, "extract_kinds": [item["kind"]], "paused_landmark": False,
        "summary": item["use_note"], "relation_note": "用于示例开发工具箱；全部内容为合成演示。",
    } for card_id, item in zip(card_ids, content, strict=True)]})
    if any(row["status"] != "accepted" for row in evaluated["results"]):
        raise RuntimeError(f"Synthetic evaluations rejected: {evaluated}")
    mcp("get_opportunity_lens")
    breakdowns = mcp("submit_opportunity_breakdowns", {"evaluator": "demo:synthetic", "items": [{
        "card_id": card_ids[2], "profile_version": 1, "monetization_model": "service",
        "offer": "虚构的文档整理服务", "price_signal": "100 个示例单位；并非真实价格",
        "funnel": "示例博客 → 服务介绍 → 报告与清单", "target_customer": "小型开源团队",
        "evidence": [content[2]["quote"]], "replicability": 60, "credibility": 30,
        "risk_flags": ["unverifiable"], "next_step": "observe",
        "verify_questions": ["实际需求频率与交付成本是多少？"],
        "breakdown_note": "纯合成线索：只有展示价，没有真实成交证据。用途是演示跟进界面，不能据此判断商机成立。",
    }]})
    if any(row["status"] != "accepted" for row in breakdowns["results"]):
        raise RuntimeError(f"Synthetic breakdown rejected: {breakdowns}")
    mcp("list_extraction_candidates", {"since": "2020-01-01T00:00:00Z"})
    knowledge_ids = []
    for card_id, item in zip(card_ids, content, strict=True):
        mcp("get_card", {"card_id": card_id})
        mcp("search_knowledge_cards", {"kind": item["kind"], "query": item["title"]})
        extracted = mcp("submit_knowledge_extractions", {"evaluator": "demo:synthetic", "items": [{
            "card_id": card_id, "profile_version": 1, "outcome": "extracted", "cards": [{
                "action": "new", "kind": item["kind"], "subtype": item["subtype"],
                "title": item["title"], "use_note": item["use_note"], "strength": 75,
                "attribution_kind": "project", "attribution_ref": "示例开发工具箱",
                "quotes": [item["quote"]], "fields": item["fields"],
            }]}]})["results"][0]
        if extracted["status"] != "accepted":
            raise RuntimeError(f"Synthetic extraction rejected: {extracted}")
        knowledge_id = extracted["cards"][0]["knowledge_id"]
        mcp("get_knowledge_card", {"knowledge_id": knowledge_id})
        knowledge_ids.append(knowledge_id)
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    day = now.date() + timedelta(days=now.hour >= 7)
    brief = api("POST", f"/api/intelligence/daily-brief/{day}/rebuild?force=true")["brief"]
    if brief["layout"] != "knowledge_v1" or len(brief["items"]) != len(content):
        raise RuntimeError("Synthetic knowledge brief is incomplete")
    extension_token = api("POST", "/api/token-auth/tokens", json={
        "name": "合成演示捕获扩展", "scopes": ["captures:write"],
    })["plaintext_token"]
    client.close()
    print("Verified: 5 finalized cards, 5 accepted evaluations, 5 knowledge cards, nonempty knowledge_v1 brief.", flush=True)
    return {"brief_date": day.isoformat(), "card_ids": card_ids, "knowledge_ids": knowledge_ids,
            "extension_token": extension_token}
