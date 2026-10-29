import { afterEach, describe, expect, it, vi } from "vitest";

import { checkPlatformAccount } from "../api/collectionEnvironment";

describe("collection environment actions", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("treats a completed needs-login check as a result, not a transport failure", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      JSON.stringify({ ok: false, platform: "x", status: "not_configured" }),
      { status: 200, headers: { "content-type": "application/json" } },
    )));

    await expect(checkPlatformAccount("x")).resolves.toMatchObject({
      ok: false,
      status: "not_configured",
    });
  });
});
