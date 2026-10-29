import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchAuthRecovery, openAuthRecoveryConsole, verifyAuthRecovery } from "../api/authRecovery";

describe("auth recovery API", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("uses the encoded incident endpoints and preserves a safe server error message", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ code: "recovery_not_actionable", message: "Auth recovery is not actionable" }), { status: 409, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(openAuthRecoveryConsole("incident/a")).rejects.toThrow("Auth recovery is not actionable");
    expect(fetchMock).toHaveBeenCalledWith("/api/auth-recoveries/incident%2Fa/open-console", expect.objectContaining({ method: "POST", signal: expect.any(AbortSignal) }));
  });

  it("returns a verification result with ok false instead of turning it into a transport error", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ ok: false, verification: { status: "failed", code: "platform_session_not_ready", checked_at: "2026-07-26T08:00:00Z" }, incident: { incident_id: "incident-1" } }), { status: 200, headers: { "content-type": "application/json" } })));

    await expect(verifyAuthRecovery("incident-1")).resolves.toMatchObject({ ok: false, verification: { code: "platform_session_not_ready" } });
  });

  it("loads an incident through the authenticated API path", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true, incident: { incident_id: "incident-1" } }), { status: 200, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchAuthRecovery("incident-1")).resolves.toMatchObject({ incident: { incident_id: "incident-1" } });
    expect(fetchMock).toHaveBeenCalledWith("/api/auth-recoveries/incident-1", expect.objectContaining({ signal: expect.any(AbortSignal) }));
  });
});
