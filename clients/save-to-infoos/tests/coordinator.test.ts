import { describe, expect, it, vi } from "vitest";
import { CaptureApiError } from "../src/shared/api";
import { CaptureCoordinator } from "../src/background/coordinator";
import type { CaptureOutcome, ExtensionConfig } from "../src/shared/types";
import { capabilities, receipt } from "./fixtures";

function testConfig(): ExtensionConfig {
  return {
    baseUrl: "https://infoos.example.com",
    token: "test-token",
    defaultMode: "url_only",
    autoCloseAfterSuccess: false,
    capabilities,
    capabilitiesCheckedAt: "2026-07-30T10:00:00.000Z"
  };
}

describe("capture coordinator", () => {
  it("reuses the same request and client_capture_id after an unconfirmed timeout", async () => {
    const requests: string[] = [];
    const outcomes: CaptureOutcome[] = [];
    const submit = vi
      .fn()
      .mockImplementationOnce(async (_base, _token, request) => {
        requests.push(request.client_capture_id);
        throw new CaptureApiError("timeout");
      })
      .mockImplementationOnce(async (_base, _token, request) => {
        requests.push(request.client_capture_id);
        return receipt;
      });
    const coordinator = new CaptureCoordinator({
      loadConfig: async () => testConfig(),
      saveCapabilities: async () => undefined,
      saveOutcome: async (outcome) => void outcomes.push(outcome),
      getPageContext: async () => ({
        url: "https://example.com/article",
        canonicalUrl: null,
        title: "Article",
        pageLanguage: "en",
        selectedText: null
      }),
      getRenderedContent: async () => {
        throw new Error("must_not_read_body");
      },
      submitCapture: submit,
      now: () => new Date("2026-07-30T10:00:00.000Z"),
      createCaptureId: () => "cap_stable",
      clientVersion: "0.1.0"
    });

    const first = await coordinator.capture(10, "url_only");
    const second = await coordinator.retry();

    expect(first.outcome.kind).toBe("unconfirmed");
    expect(first.canRetry).toBe(true);
    expect(second.outcome.kind).toBe("accepted");
    expect(requests).toEqual(["cap_stable", "cap_stable"]);
    expect(outcomes.map((outcome) => outcome.kind)).toEqual([
      "unconfirmed",
      "accepted"
    ]);
  });

  it("never reads readable content in URL-only mode", async () => {
    const getRenderedContent = vi.fn();
    const coordinator = new CaptureCoordinator({
      loadConfig: async () => testConfig(),
      saveCapabilities: async () => undefined,
      saveOutcome: async () => undefined,
      getPageContext: async () => ({
        url: "https://example.com",
        canonicalUrl: null,
        title: "Example",
        pageLanguage: null,
        selectedText: null
      }),
      getRenderedContent,
      submitCapture: async () => receipt,
      now: () => new Date("2026-07-30T10:00:00.000Z"),
      createCaptureId: () => "cap_url_only",
      clientVersion: "0.1.0"
    });

    await coordinator.capture(1, "url_only");
    expect(getRenderedContent).not.toHaveBeenCalled();
  });

  it("offers a fresh URL-only request when readable content exceeds the limit", async () => {
    const submittedIds: string[] = [];
    let captureIdIndex = 0;
    const coordinator = new CaptureCoordinator({
      loadConfig: async () => ({
        ...testConfig(),
        capabilities: {
          ...capabilities,
          limits: { ...capabilities.limits, rendered_content_bytes: 30 }
        }
      }),
      saveCapabilities: async () => undefined,
      saveOutcome: async () => undefined,
      getPageContext: async () => ({
        url: "https://example.com/large",
        canonicalUrl: null,
        title: "Large article",
        pageLanguage: "en",
        selectedText: null
      }),
      getRenderedContent: async () => ({
        format: "readability_html",
        html: `<article>${"content".repeat(20)}</article>`,
        text: "content".repeat(20)
      }),
      submitCapture: async (_base, _token, request) => {
        submittedIds.push(request.client_capture_id);
        return receipt;
      },
      now: () => new Date("2026-07-30T10:00:00.000Z"),
      createCaptureId: () => `cap_${++captureIdIndex}`,
      clientVersion: "0.1.0"
    });

    const first = await coordinator.capture(1, "readable_content");
    const second = await coordinator.downgrade();

    expect(first.outcome.kind).toBe("too_large");
    expect(first.canDowngrade).toBe(true);
    expect(second.outcome.kind).toBe("accepted");
    expect(second.outcome.mode).toBe("url_only");
    expect(submittedIds).toEqual(["cap_2"]);
  });
});
