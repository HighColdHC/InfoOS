import { describe, expect, it } from "vitest";
import { truncateUtf8, utf8ByteLength } from "../src/shared/bytes";
import {
  buildCaptureRequest,
  PayloadValidationError
} from "../src/shared/payload";
import { capabilities } from "./fixtures";

const context = {
  url: "https://example.com/article",
  canonicalUrl: "https://example.com/canonical",
  title: "测试文章",
  pageLanguage: "zh-CN",
  selectedText: "一段选区"
};

describe("capture request construction", () => {
  it("uses the frozen field whitelist and keeps URL mode body-free", () => {
    const request = buildCaptureRequest({
      context,
      mode: "url_only",
      renderedContent: null,
      capabilities,
      clientCaptureId: "cap_fixed",
      clientVersion: "0.1.0",
      capturedAt: "2026-07-30T10:00:00+08:00"
    });

    expect(request).toEqual({
      schema: "infoos.capture-request.v1",
      client_capture_id: "cap_fixed",
      url: "https://example.com/article",
      canonical_url: "https://example.com/canonical",
      title: "测试文章",
      selected_text: "一段选区",
      page_language: "zh-CN",
      captured_at: "2026-07-30T10:00:00+08:00",
      capture_mode: "url_only",
      rendered_content: null,
      client: {
        name: "save-to-infoos",
        version: "0.1.0"
      }
    });
    expect(request).not.toHaveProperty("cookies");
    expect(request).not.toHaveProperty("storage");
    expect(request).not.toHaveProperty("html");
  });

  it("enforces server-declared content limits", () => {
    expect(() =>
      buildCaptureRequest({
        context,
        mode: "readable_content",
        renderedContent: {
          format: "readability_html",
          html: `<article>${"很长".repeat(20)}</article>`,
          text: "很长".repeat(20)
        },
        capabilities: {
          ...capabilities,
          limits: { ...capabilities.limits, rendered_content_bytes: 20 }
        },
        clientCaptureId: "cap_fixed",
        clientVersion: "0.1.0"
      })
    ).toThrowError(new PayloadValidationError("content_too_large"));
  });

  it("truncates UTF-8 text without splitting surrogate pairs", () => {
    const value = truncateUtf8("中文🙂abc", 10);
    expect(value).toBe("中文🙂");
    expect(utf8ByteLength(value)).toBeLessThanOrEqual(10);
  });
});
