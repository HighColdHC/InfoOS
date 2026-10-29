#!/usr/bin/env python3
"""Photograph real local views in dark mode, after DOM redaction."""
from __future__ import annotations

import argparse
import io
import json
import re
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image
from playwright.sync_api import sync_playwright

from readme_redaction import LeakAudit, corpus, policy, redact

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs/images/features"


def select_views(database: Path) -> dict:
    with sqlite3.connect(database) as db:
        brief_date, count = db.execute("""SELECT brief_date,
            coalesce(json_array_length(items_json,'$.items'),json_array_length(items_json)) n
            FROM daily_briefs ORDER BY n DESC, brief_date DESC LIMIT 1""").fetchone()
        knowledge_id, evidence_count, card_id = db.execute("""SELECT k.knowledge_id,
            count(e.evidence_id) n, min(e.card_id) FROM knowledge_cards k
            JOIN knowledge_evidence e USING(knowledge_id)
            WHERE k.superseded_by IS NULL GROUP BY k.knowledge_id
            ORDER BY n DESC, length(k.fields_json) DESC LIMIT 1""").fetchone()
        totals = {table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                  for table in ("sources", "cards", "knowledge_cards", "tasks", "opportunity_breakdowns")}
        kinds = dict(db.execute("SELECT kind,count(*) FROM knowledge_cards GROUP BY kind"))
        card_id = db.execute("""SELECT card_id FROM cards WHERE card_id IN
            (SELECT card_id FROM knowledge_evidence WHERE knowledge_id=?)
            ORDER BY length(content) DESC LIMIT 1""", (knowledge_id,)).fetchone()[0]
    return {"brief_date": brief_date, "brief_items": count, "knowledge_id": knowledge_id,
            "evidence_count": evidence_count, "card_id": card_id, "knowledge_kinds": kinds, "totals": totals}


def capture(base_url: str, runtime: Path, phase: str):
    if urlsplit(base_url).hostname != "127.0.0.1":
        raise ValueError("Use only the disposable loopback demo")
    values = corpus(runtime / "data/infoos.sqlite3")
    audit = LeakAudit(values)
    report = {"phase": phase, "corpus_counts": {k: len(v) for k, v in values.items()}, "screenshots": []}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1,
                                      locale="zh-CN", timezone_id="Asia/Shanghai", color_scheme="dark",
                                      reduced_motion="reduce", service_workers="block")
        context.add_init_script("localStorage.setItem('infoos.theme','dark')")
        denied = []

        def allow_local_read(route):
            request = route.request
            if urlsplit(request.url).netloc != urlsplit(base_url).netloc or request.method not in {"GET", "HEAD"}:
                denied.append(request.method)
                route.abort()
            else:
                route.continue_()

        context.route("**/*", allow_local_read)
        page = context.new_page()
        errors = []
        def visit(path):
            nonlocal page
            page.close()
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(type(error).__name__))
            page.goto(base_url + path, wait_until="networkidle")
            page.evaluate("document.fonts.ready")
            page.get_by_text("正在加载正文…", exact=True).wait_for(state="hidden")
            if page.get_by_role("heading", name="InfoOS 首次设置", exact=True).is_visible() and phase == "live":
                page.get_by_role("button", name="关闭对话框", exact=True).click()
            if page.locator("html").get_attribute("data-theme") != "dark":
                raise RuntimeError("Product dark theme did not activate")

        def shot(name, view):
            assert page.get_by_text("部分处理异常", exact=False).count() == 0, "Collector still shows degraded status"
            # Freeze product scripts so polling cannot restore private text after redaction.
            cdp = context.new_cdp_session(page)
            cdp.send("Emulation.setScriptExecutionDisabled", {"value": True})
            metrics = page.locator(",".join(policy()["preserve_selectors"]))
            before = metrics.all_text_contents()
            ui_nodes = r"""rules => {
                const ui = new Set(rules.interface_literals), nodes = [];
                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                while (walker.nextNode()) {
                    const n = walker.currentNode;
                    if (n.parentElement?.closest([...rules.selectors, ...rules.identifier_selectors].join(','))) continue;
                    if (ui.has(n.nodeValue.trim().replace(/\s+/g, ' '))) nodes.push(n.nodeValue);
                }
                return nodes;
            }"""
            interface = page.evaluate(ui_nodes, policy())
            numeric_nodes = r"""selectors => {
                const out = [], walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                while (walker.nextNode()) {
                    if (walker.currentNode.parentElement?.closest(selectors.join(','))) continue;
                    const value = walker.currentNode.nodeValue.trim();
                    if (value && /^[\d\s,.:%+−—/年月日时分秒\-]+$/.test(value)
                        && !/^\d{1,3}\.\d{1,3}\./.test(value)) out.push(value);
                }
                return out;
            }"""
            numbers = page.evaluate(numeric_nodes, policy()["identity_selectors"] + policy()["identifier_selectors"])
            appearance = page.evaluate("[getComputedStyle(document.body).backgroundColor, document.styleSheets.length]")
            privacy = redact(page, values, audit)
            visible_text = page.locator("body").inner_text()
            assert "record_example" not in visible_text and not re.search(r"[（(]\d+[）)]", "\n".join(page.locator("h1,h2,h3").all_text_contents())), "Placeholder or numeric title suffix remains"
            if metrics.all_text_contents() != before:
                after = metrics.all_text_contents()
                changed = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
                structure = [metrics.nth(i).evaluate("e => ({tag:e.tagName, category:e.className})") for i in changed]
                raise RuntimeError("Redaction changed a statistic or interface label: " + json.dumps(structure))
            if page.evaluate(ui_nodes, policy()) != interface:
                raise RuntimeError("Redaction changed static product interface text")
            if page.evaluate(numeric_nodes, policy()["identity_selectors"] + policy()["identifier_selectors"]) != numbers:
                raise RuntimeError("Redaction changed a numeric/date text node")
            if page.evaluate("[getComputedStyle(document.body).backgroundColor, document.styleSheets.length]") != appearance:
                raise RuntimeError("Redaction changed the product stylesheet")
            if name == "knowledge-detail":
                page.get_by_role("heading", name="原文证据", exact=True).evaluate(
                    "e => window.scrollBy(0, e.getBoundingClientRect().top - 64)")
            page.mouse.move(0, 0)
            raw = page.screenshot(animations="disabled")
            # Keep scripts disabled until visit() closes this disposable page.
            path = OUTPUT / f"{name}.webp"
            with Image.open(io.BytesIO(raw)) as source:
                pixels = Image.frombytes("RGB", source.size, source.convert("RGB").tobytes())
                pixels.save(path, lossless=True, method=6)
            if path.stat().st_size > 400_000:
                raise ValueError(f"Image exceeds 400 KB: {name}")
            report["screenshots"].append({"name": name, "view": view, "bytes": path.stat().st_size, "privacy": privacy,
                                          "preserved_metric_nodes": len(before), "preserved_ui_nodes": len(interface), "numeric_nodes": numbers})
            print(f"Captured {name}: {path.stat().st_size} bytes", flush=True)

        def completed_runs():
            with page.expect_response(lambda response: "/api/source-runs?" in response.url
                                      and "status=completed" in response.url) as loaded:
                page.get_by_label("状态筛选", exact=True).select_option("completed")
            assert loaded.value.ok, "Completed run filter failed"
            page.get_by_text("正在刷新来源运行…", exact=True).wait_for(state="hidden")
            page.wait_for_function("""() => {
                const states = [...document.querySelectorAll('.collector-source-run-table .collector-task-status')];
                return states.length > 0 && states.every(el => el.textContent.includes('已完成'));
            }""")

        if phase == "empty":
            visit("/")
            page.get_by_role("heading", name="InfoOS 首次设置", exact=True).wait_for()
            shot("onboarding", "空库首次设置")
        else:
            selected = select_views(runtime / "data/infoos.sqlite3")
            report["selection"] = {key: value for key, value in selected.items() if not key.endswith("_id")}
            entries = [
                (f"/r/brief/{selected['brief_date']}", "daily-brief", "条目最多的冻结日报"),
                ("/r/knowledge", "knowledge-library", "全部五类知识与真实计数"),
                (f"/r/knowledge/{selected['knowledge_id']}", "knowledge-detail", "最多原文证据的知识卡"),
                ("/r/browse", "card-browse", "最新推荐信息卡"),
                (f"/r/card/{selected['card_id']}", "card-detail", "多证据知识卡对应的原始信息卡"),
                ("/?menu=cards", "card-management", "真实信息卡管理列表"),
                ("/?menu=sources", "source-management", "真实来源统计与会话快照"),
                ("/?menu=tasks", "collection-tasks", "有完成证据的采集运行"),
                ("/?menu=environment", "collection-environment", "真实组件版本、会话快照与通知空态"),
                ("/?menu=logs", "export-and-logs", "导出能力与本实例日志"),
                ("/r/opportunities", "opportunity-board", "历史商机的跟进看板"),
                ("/r/opportunities/stats", "opportunity-stats", "历史商机统计"),
                ("/?menu=tokens", "token-permissions", "已清除真实凭据的权限管理"),
                ("/?menu=account", "account-settings", "隔离临时账号表单"),
            ]
            context.set_extra_http_headers({"x-infoos-edge-authenticated": "1"})
            status = context.request.get(base_url + "/api/collector-status")
            assert status.ok
            report["collector_status"] = status.json()
            assert report["collector_status"]["service_status"] == "ready", "Collector is not ready"
            for path, name, description in entries:
                visit(path)
                if name == "source-management":
                    response = context.request.get(base_url + "/api/configurable-sources?limit=1")
                    assert response.ok
                    actual = response.json()["summary"]
                    expected = json.loads((runtime.parent / "runtime-facts.json").read_text())["source_summary"]
                    assert all(actual[k] == expected[k] for k in ("total_sources", "enabled_sources", "needs_attention")), "Source summary differs from live facts"
                    assert round(actual["reliability_7d"] * 100, 1) == round(expected["reliability_7d"] * 100, 1)
                    report["source_summary"] = actual
                    page.get_by_text("近 7 天全部来源的运行成功率", exact=True).wait_for()
                    for width in (1440, 375):
                        page.set_viewport_size({"width":width,"height":900})
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Source page overflows viewport"
                        note = page.locator(".metric-card__description").bounding_box()
                        assert note and note["x"] >= 0 and note["x"] + note["width"] <= width, "Metric note overflows"
                    page.set_viewport_size({"width":1440,"height":900})
                if name == "collection-tasks":
                    completed_runs()
                if name == "collection-environment":
                    response = context.request.get(base_url + "/api/collection-environment")
                    assert response.ok
                    environment = response.json()
                    assert not any(c["state"] != "closed" for c in environment["runtime_circuits"]), "Active runtime protection remains"
                    report["components"] = environment["components"]
                    expected = json.loads((runtime.parent / "runtime-facts.json").read_text())["components"]
                    for component in environment["components"]:
                        saved = expected[component["id"]]
                        assert component["status"] == saved["status"] and component["version"] == saved["version"]
                        assert component["last_checked_at"] == saved["last_checked"]
                    bridge = next(c for c in environment["components"] if c["id"] == "opencli_bridge")
                    assert bridge["status"] == "connected" and bridge["version"] == "1.0.26" and bridge["last_checked_at"]
                    assert page.locator(".collector-environment-protection").count() == 0
                if name == "knowledge-detail":
                    page.get_by_role("heading", name="原文证据", exact=True).evaluate(
                        "element => {element.scrollIntoView({block: 'start'}); window.scrollBy(0, -180)}")
                if name == "card-management":
                    item = page.locator('.card-feed-item[data-evaluation="passed"]').first
                    card_id = item.get_attribute("data-card-id")
                    item.locator("button").first.click()
                    page.wait_for_function("id => document.querySelector('.collector-card-detail__facts code')?.textContent === id", arg=card_id)
                    page.locator(".card-eval__meters").wait_for()
                if name == "opportunity-board":
                    page.get_by_label("新推荐范围").select_option("false")
                    page.locator(".board[aria-busy='false']").wait_for()
                if name in {"collection-environment", "daily-brief", "knowledge-library", "collection-tasks", "opportunity-stats"}:
                    page.set_viewport_size({"width": 1440, "height": 1400})
                if name in {"card-management", "card-detail"}:
                    page.set_viewport_size({"width": 1440, "height": 1200})
                if name in {"card-browse", "opportunity-board"}:
                    page.set_viewport_size({"width": 1440, "height": 1100})
                shot(name, description)
                page.set_viewport_size({"width": 1440, "height": 900})
                if name == "collection-tasks":
                    visit(path)
                    completed_runs()
                    # Score only the actual runs shown by the product filter.
                    response = context.request.get(base_url + "/api/source-runs?status=completed&limit=25")
                    assert response.ok, "Run selection request failed"
                    runs = response.json()["items"]
                    best = max(runs, key=lambda run: (run["card_progress"].get("complete") or 0,
                                                      run.get("duration_ms") or 0))
                    page.get_by_role("button", name="追踪 " + best["source_name"], exact=True).first.click()
                    page.get_by_role("dialog").wait_for()
                    page.wait_for_load_state("networkidle")
                    page.locator(".collector-task-trace").wait_for()
                    shot("run-trace", "当前完成运行中完善卡片最多的完整链路")
            visit("/r/knowledge")
            page.set_viewport_size({"width": 390, "height": 844})
            shot("knowledge-library-mobile", "390×844 全部知识类型计数与卡片")
        context.close()
        browser.close()
        if errors:
            raise RuntimeError(f"Browser errors: {len(errors)}")
        report["blocked_requests"] = len(denied)
        (runtime / "capture-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({"phase": phase, "privacy": [
            {"name": s["name"], **s["privacy"]} for s in report["screenshots"]
        ], "blocked_requests": len(denied)}, ensure_ascii=False), flush=True)
        if sum(path.stat().st_size for path in OUTPUT.glob("*.webp")) > 5_000_000:
            raise RuntimeError("Screenshot total exceeds 5 MB")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--phase", choices=["empty", "live"], required=True)
    args = parser.parse_args()
    capture(args.base_url, args.runtime_dir, args.phase)
