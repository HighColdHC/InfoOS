// @vitest-environment jsdom

import { describe, expect, it } from "vitest";
import {
  extractReadablePage,
  sanitizeReadableHtml
} from "../src/content/extractor";

describe("readable page extraction", () => {
  it("removes scripts, event handlers, forms, values and unsafe URLs", () => {
    document.head.innerHTML = `
      <title>Private article</title>
      <base href="https://example.com/private">
      <meta name="author" content="Tester">
    `;
    document.body.innerHTML = `
      <main>
        <article>
          <h1>Private article</h1>
          <p>${"正文内容足够长。".repeat(10)}</p>
          <form>
            <input name="password" value="super-secret">
            <textarea>mfa-123456</textarea>
          </form>
          <a href="javascript:alert(1)" onclick="steal()">bad</a>
          <img src="/cover.jpg" onerror="steal()" alt="cover">
          <script>localStorage.getItem("token")</script>
        </article>
      </main>
    `;

    const result = extractReadablePage(document, "https://example.com/private");

    expect(result.format).toBe("readability_html");
    expect(result.text).toContain("正文内容足够长");
    expect(result.html).toContain("https://example.com/cover.jpg");
    expect(result.html).not.toContain("super-secret");
    expect(result.html).not.toContain("mfa-123456");
    expect(result.html).not.toContain("javascript:");
    expect(result.html).not.toContain("onclick");
    expect(result.html).not.toContain("<script");
    expect(result.html).not.toContain("<form");
  });

  it("unwraps unknown markup but preserves its safe text", () => {
    const html = sanitizeReadableHtml(
      '<custom-box data-secret="x"><p>Safe</p></custom-box>',
      "https://example.com",
      new DOMParser()
    );
    expect(html).toContain("<p>Safe</p>");
    expect(html).not.toContain("custom-box");
    expect(html).not.toContain("data-secret");
  });
});
