#!/usr/bin/env python3
"""Drive only disposable Playwright Chromium; never seed or alter product responses."""
from __future__ import annotations

import argparse
import io
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs/images/features"


def capture(base_url: str, runtime: Path, phase: str):
    if urlsplit(base_url).hostname != "localhost":
        raise ValueError("Use only the disposable localhost demo")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1,
                                      locale="zh-CN", timezone_id="Asia/Shanghai", color_scheme="light",
                                      reduced_motion="reduce")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def visit(path):
            page.goto(base_url + path, wait_until="networkidle")
            page.evaluate("document.fonts.ready")

        def shot(name):
            text = page.locator("body").inner_text()
            # Full identifiers are checked separately during artifact review.
            if re.search(r"https?://(?!example\.(com|org|net)|localhost(?=[:/\s]))[^\s)]+|(?:\d{1,3}\.){3}\d{1,3}|ios_[A-Za-z0-9_-]{8,}", text):
                raise ValueError(f"Unexpected address or credential in {name}")
            path = OUTPUT / f"{name}.webp"
            raw = page.screenshot(animations="disabled")
            # Lossless pixels only: strip all text, EXIF and ancillary metadata.
            with Image.open(io.BytesIO(raw)) as source:
                pixels = Image.frombytes("RGB", source.size, source.convert("RGB").tobytes())
                pixels.save(path, lossless=True, method=6)
            if path.stat().st_size > 400_000:
                raise ValueError(f"Image exceeds 400 KB: {name}")
            print(f"Captured {name}: {path.stat().st_size} bytes", flush=True)

        if phase == "empty":
            visit("/")
            page.get_by_role("heading", name="InfoOS 首次设置", exact=True).wait_for()
            shot("onboarding")
            page.get_by_role("button", name="关闭对话框", exact=True).click()
            visit("/?menu=tokens")
            shot("token-permissions")
            # Account endpoint trusts the private gateway's verdict. This local
            # development screenshot demonstrates the form, not gateway login.
            context.set_extra_http_headers({"x-infoos-edge-authenticated": "1"})
            visit("/?menu=account")
            page.get_by_text("demo-admin", exact=True).wait_for()
            shot("account-settings")
        else:
            manifest = json.loads((runtime / "manifest.json").read_text())
            entries = [
                (f"/r/brief/{manifest['brief_date']}", "daily-brief"),
                ("/r/knowledge", "knowledge-library"),
                (f"/r/knowledge/{manifest['knowledge_ids'][3]}", "knowledge-detail"),
                ("/r/browse", "card-browse"),
                (f"/r/card/{manifest['card_ids'][3]}", "card-detail"),
                ("/?menu=cards", "card-management"),
                ("/?menu=sources", "source-management"),
                ("/?menu=tasks", "collection-tasks"),
                ("/?menu=environment", "collection-environment"),
                ("/?menu=logs", "export-and-logs"),
                ("/r/opportunities", "opportunity-board"),
                ("/r/opportunities/stats", "opportunity-stats"),
            ]
            for path, name in entries:
                visit(path)
                if name == "opportunity-board":
                    page.get_by_label("新推荐范围").select_option("true")
                    page.get_by_text("虚构的文档整理服务", exact=True).wait_for()
                    page.locator(".board[aria-busy='false']").wait_for()
                if name == "collection-environment":
                    shot("notification-settings")
                    page.get_by_role("heading", name="系统组件", exact=True).evaluate(
                        "element => element.scrollIntoView({block: 'start'})")
                shot(name)
                if name == "collection-tasks":
                    page.get_by_role("button", name=re.compile("^追踪 ")).first.click()
                    page.get_by_role("dialog").wait_for()
                    page.wait_for_load_state("networkidle")
                    shot("run-trace")
            page.set_viewport_size({"width": 390, "height": 844})
            visit("/r/knowledge")
            shot("knowledge-library-mobile")
        context.close()
        browser.close()
        if errors:
            raise RuntimeError(f"Browser errors: {errors}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--phase", choices=["empty", "seeded"], required=True)
    args = parser.parse_args()
    capture(args.base_url, args.runtime_dir, args.phase)
