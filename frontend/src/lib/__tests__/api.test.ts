import { describe, it, expect, vi, beforeEach } from "vitest";
import { loadSetupState, postSetupLaunch, postSetupVerifyExtension, postSetupVerifyPlatform, postSetupSkipPlatform, postSetupFirstFetch, postSetupReset, postSetupDismiss, loadBrowserVisibility, saveBrowserVisibility } from "../api";

declare global { var global: typeof globalThis; }

describe("active api", () => {
  beforeEach(() => { vi.restoreAllMocks(); });

  it("loadSetupState hits /api/setup/status", async () => {
    const fetchSpy = vi
      .spyOn(global, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({ current_step: "fresh" })));
    const state = await loadSetupState();
    expect(fetchSpy).toHaveBeenCalledWith("/api/setup/status", expect.objectContaining({ signal: expect.any(AbortSignal) }));
    expect(state.current_step).toBe("fresh");
  })

  it("postSetupLaunch POSTs to /api/setup/launch", async () => {
    const fetchSpy = vi
      .spyOn(global, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({ ok: true, console_url: "/browser-console/" })));
    const result = await postSetupLaunch();
    expect(fetchSpy).toHaveBeenCalledWith("/api/setup/launch", expect.objectContaining({ method: "POST", signal: expect.any(AbortSignal) }));
    expect(result.ok).toBe(true);
    expect(result).toEqual({ ok: true, console_url: "/browser-console/" });
  })

  it("postSetupVerifyExtension POSTs to /api/setup/verify-extension", async () => {
    const fetchSpy = vi
      .spyOn(global, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({ ok: true })));
    const result = await postSetupVerifyExtension();
    expect(fetchSpy).toHaveBeenCalledWith("/api/setup/verify-extension", expect.objectContaining({ method: "POST", signal: expect.any(AbortSignal) }));
    expect(result.ok).toBe(true);
  })

  it("postSetupVerifyPlatform POSTs to /api/setup/verify/<platform>", async () => {
    const fetchSpy = vi
      .spyOn(global, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({
        ok: true,
        platform: "x",
        status: "verified",
        last_checked: "2026-07-22T00:00:00Z",
        error: null,
        persisted: true,
        persistence_checked_at: "2026-07-22T00:00:01Z",
        persistence_stage: "verified",
      })));
    const result = await postSetupVerifyPlatform("x");
    expect(fetchSpy).toHaveBeenCalledWith("/api/setup/verify/x", expect.objectContaining({ method: "POST", signal: expect.any(AbortSignal) }));
    expect(result.status).toBe("verified");
    expect(result.persisted).toBe(true);
    expect(result.persistence_stage).toBe("verified");
  })

  it("postSetupSkipPlatform POSTs to /api/setup/skip/<platform>", async () => {
    const fetchSpy = vi
      .spyOn(global, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({ status: "skipped" })));
    const result = await postSetupSkipPlatform("youtube");
    expect(fetchSpy).toHaveBeenCalledWith("/api/setup/skip/youtube", expect.objectContaining({ method: "POST", signal: expect.any(AbortSignal) }));
    expect(result.status).toBe("skipped");
  })

  it("postSetupFirstFetch POSTs to /api/setup/first-fetch", async () => {
    const fetchSpy = vi
      .spyOn(global, "fetch")
      .mockResolvedValue(
        new Response(JSON.stringify({ ok: true, current_step: "complete", fetched_count: 5 }))
      );
    const result = await postSetupFirstFetch();
    expect(fetchSpy).toHaveBeenCalledWith("/api/setup/first-fetch", expect.objectContaining({ method: "POST", signal: expect.any(AbortSignal) }));
    expect(result.ok).toBe(true);
    expect(result.current_step).toBe("complete");
    expect(result.fetched_count).toBe(5);
  })

  it("postSetupReset POSTs to /api/setup/reset", async () => {
    const fetchSpy = vi
      .spyOn(global, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({ current_step: "fresh", completed_steps: [] })));
    const result = await postSetupReset();
    expect(fetchSpy).toHaveBeenCalledWith("/api/setup/reset", expect.objectContaining({ method: "POST", signal: expect.any(AbortSignal) }));
    expect(result.current_step).toBe("fresh");
  })

  it("postSetupDismiss POSTs to /api/setup/dismiss", async () => {
    const fetchSpy = vi
      .spyOn(global, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({ current_step: "skipped" })));
    const result = await postSetupDismiss();
    expect(fetchSpy).toHaveBeenCalledWith("/api/setup/dismiss", expect.objectContaining({ method: "POST", signal: expect.any(AbortSignal) }));
    expect(result.current_step).toBe("skipped");
  })

  it("loadBrowserVisibility GETs /api/browser/visibility", async () => {
    const fetchSpy = vi
      .spyOn(global, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({ headless_during_fetch: true })));
    const result = await loadBrowserVisibility();
    expect(fetchSpy).toHaveBeenCalledWith("/api/browser/visibility", expect.objectContaining({ signal: expect.any(AbortSignal) }));
    expect(result.headless_during_fetch).toBe(true);
  })

  it("saveBrowserVisibility PUTs the value", async () => {
    const fetchSpy = vi
      .spyOn(global, "fetch")
      .mockResolvedValue(new Response(null, { status: 204 }));
    await saveBrowserVisibility(true);
    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe("/api/browser/visibility");
    expect(init?.method).toBe("PUT");
    expect(JSON.parse(init?.body as string)).toEqual({ headless_during_fetch: true });
  })

});
