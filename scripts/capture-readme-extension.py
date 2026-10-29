#!/usr/bin/env python3
"""Drive the actual unpacked extension against a fictional page and disposable API."""
from __future__ import annotations

import argparse
import html
import io
import json
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]


def capture(base_url: str, runtime: Path):
    if not base_url.startswith("http://localhost:"):
        raise ValueError("Only the disposable localhost instance is allowed")
    extension = runtime / "extension"
    if not (extension / "manifest.json").exists():
        raise RuntimeError("Build clients/save-to-infoos first")
    manifest = json.loads((runtime / "manifest.json").read_text())
    content = json.loads((ROOT / "scripts/readme-demo-content.json").read_text())[3]
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(runtime / "playwright-extension-profile"), channel="chromium", headless=True,
            viewport={"width": 390, "height": 844}, locale="zh-CN", color_scheme="light",
            args=[f"--disable-extensions-except={extension}", f"--load-extension={extension}",
                  "--enable-unsafe-extension-debugging"],
        )
        try:
            worker = context.service_workers[0] if context.service_workers else context.wait_for_event("serviceworker")
            extension_id = worker.url.split("/")[2]
            options = context.new_page()
            options.goto(f"chrome-extension://{extension_id}/options.html")
            options.locator("#base-url").fill(base_url)
            options.locator("#token").fill(manifest["extension_token"])
            options.locator("#default-mode").select_option("readable_content")
            options.get_by_role("button", name="保存并测试连接", exact=True).click()
            options.locator("#connection-result").wait_for()
            if "成功" not in options.locator("#connection-result").inner_text():
                raise RuntimeError("Extension connection failed: " + options.locator("#connection-result").inner_text())
            # Browser-only synthetic article; no real external page is fetched.
            article = context.new_page()
            markup = '<meta charset="utf-8"><title>示例博客：原型验证方法</title><article><h1>示例博客：原型验证方法</h1>'
            markup += "".join(f"<p>{html.escape(paragraph)}</p>" for paragraph in content["body"].split("\n\n"))
            markup += "</article>"
            article.route("https://example.com/**", lambda route: route.fulfill(
                content_type="text/html; charset=utf-8", body=markup))
            article.goto("https://example.com/articles/capture-demo")
            cdp = context.browser.new_browser_cdp_session()
            targets = cdp.send("Target.getTargets", {"filter": [
                {"type": "tab", "exclude": False}, {"exclude": True},
            ]})["targetInfos"]
            target = next(item["targetId"] for item in targets if item["url"] == article.url)
            # Grant activeTab by invoking the real action on the synthetic tab.
            cdp.send("Extensions.triggerAction", {"id": extension_id, "targetId": target})
            popup = context.new_page()
            article.bring_to_front()
            popup.goto(f"chrome-extension://{extension_id}/popup.html")
            popup.get_by_text("示例博客：原型验证方法", exact=True).wait_for()
            popup.get_by_role("button", name="交给 InfoOS", exact=True).click()
            popup.locator("#status-label").filter(has_text="已接收").wait_for()
            # Capture the original popup document in an isolated tab, with its
            # narrow layout intact. Secret fields live only on the options tab.
            path = ROOT / "docs/images/features/web-capture.webp"
            with Image.open(io.BytesIO(popup.screenshot(animations="disabled"))) as source:
                pixels = Image.frombytes("RGB", source.size, source.convert("RGB").tobytes())
                pixels.save(path, lossless=True, method=6)
            if path.stat().st_size > 400_000:
                raise ValueError("Extension screenshot exceeds 400 KB")
            print(f"Captured web-capture: {path.stat().st_size} bytes", flush=True)
        finally:
            context.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    args = parser.parse_args()
    capture(args.base_url, args.runtime_dir)
