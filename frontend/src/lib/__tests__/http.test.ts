import { afterEach, describe, expect, it, vi } from "vitest";
import { getJson, postJson, sendEmpty, sendJson } from "../api/http";
import { loadSetupState } from "../api/setup";
import { loadBrowserVisibility } from "../api/browserVisibility";
import { loadPlatformCapabilities } from "../api/platformCapabilities";
import { fetchSourcePage, fetchSourceDetail, fetchSourcePlatformCapabilities } from "../api/sourceManagement";
import { fetchResearchCapabilities, fetchResearchRuns, fetchResearchRun } from "../api/research";
import { fetchTokenOverview, fetchAdminTokens, fetchRequestLogs, fetchRequestLogDetail } from "../api/tokenAuth";
import { fetchAuthRecovery } from "../api/authRecovery";

describe("HTTP timeout semantics", () => {
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

  it("getJson defaults to exactly 10 seconds and cleans its timer", async () => {
    vi.useFakeTimers();
    let aborted = false;
    vi.stubGlobal("fetch", vi.fn((_path, options) => new Promise((_resolve, reject) => {
      options?.signal?.addEventListener("abort", () => {
        aborted = true;
        reject(new DOMException("Aborted", "AbortError"));
      });
    })));
    const pending = getJson("/slow");
    const rejection = expect(pending).rejects.toMatchObject({ kind: "timeout" });
    await vi.advanceTimersByTimeAsync(9_999);
    expect(aborted).toBe(false);
    expect(vi.getTimerCount()).toBe(1);
    await vi.advanceTimersByTimeAsync(1);
    await rejection;
    expect(aborted).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("propagates in-flight caller abort as AbortError regardless of reason", async () => {
    vi.useFakeTimers();
    const caller = new AbortController();
    vi.stubGlobal("fetch", vi.fn((_path, options) => new Promise((_resolve, reject) => {
      options?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
    })));
    const pending = getJson("/abort", caller.signal);
    caller.abort("custom reason");
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(vi.getTimerCount()).toBe(0);
  });

  it("does not fetch or allocate a timer for a pre-aborted signal", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const caller = new AbortController();
    caller.abort(new Error("arbitrary"));
    await expect(getJson("/never", caller.signal)).rejects.toMatchObject({ name: "AbortError" });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("cleans the timeout after a successful response", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn(async () => new Response('{"ok":true}')));
    await expect(getJson<{ ok: boolean }>("/ok")).resolves.toEqual({ ok: true });
    expect(vi.getTimerCount()).toBe(0);
  });

  it.each([
    ["sendJson", () => sendJson("/write", "PUT", {})],
    ["postJson", () => postJson("/write", {})],
    ["sendEmpty", () => sendEmpty("/write", "PUT", {})],
  ] as const)("%s writes have no default timeout", async (_name, invoke) => {
    await expectNoTimeout(invoke);
  });

  it.each([
    ["getJson(null)", () => getJson("/read", undefined, null)],
    ["setup status", loadSetupState],
    ["browser visibility", loadBrowserVisibility],
    ["platform capabilities", loadPlatformCapabilities],
    ["source page", () => fetchSourcePage()],
    ["source detail", () => fetchSourceDetail("source")],
    ["source capabilities", fetchSourcePlatformCapabilities],
    ["research capabilities", fetchResearchCapabilities],
    ["research runs", fetchResearchRuns],
    ["research detail", () => fetchResearchRun("run")],
    ["token overview", fetchTokenOverview],
    ["admin tokens", fetchAdminTokens],
    ["request logs", () => fetchRequestLogs()],
    ["request log detail", () => fetchRequestLogDetail("request")],
    ["auth recovery", () => fetchAuthRecovery("incident")],
  ] as const)("%s explicitly keeps reads without a timeout", async (_name, invoke) => {
    await expectNoTimeout(invoke);
  });
});

async function expectNoTimeout(invoke: () => Promise<unknown>): Promise<void> {
  vi.useFakeTimers();
  let respond!: (response: Response) => void;
  let signal!: AbortSignal;
  vi.stubGlobal("fetch", vi.fn((_path, options) => new Promise<Response>((resolve, reject) => {
    respond = resolve;
    signal = options.signal;
    signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
  })));
  const pending = invoke();
  let settled = false;
  const outcome = pending.then(
    value => { settled = true; return { value }; },
    error => { settled = true; return { error }; },
  );
  expect(vi.getTimerCount()).toBe(0);
  await vi.advanceTimersByTimeAsync(60_000);
  expect(signal.aborted).toBe(false);
  expect(settled).toBe(false);
  respond(new Response('{"ok":true,"platforms":[],"items":[]}'));
  expect(await outcome).toHaveProperty("value");
  expect(vi.getTimerCount()).toBe(0);
}
