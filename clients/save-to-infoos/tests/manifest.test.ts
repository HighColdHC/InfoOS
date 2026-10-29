import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

interface Manifest {
  manifest_version: number;
  permissions: string[];
  optional_host_permissions: string[];
  content_scripts?: unknown[];
  content_security_policy: { extension_pages: string };
}

async function loadManifest(): Promise<Manifest> {
  return JSON.parse(
    await readFile(resolve(process.cwd(), "public/manifest.json"), "utf8")
  ) as Manifest;
}

describe("Manifest V3 permission audit", () => {
  it("uses user-triggered access without persistent content scripts", async () => {
    const manifest = await loadManifest();
    expect(manifest.manifest_version).toBe(3);
    expect(manifest.permissions.sort()).toEqual(
      ["activeTab", "contextMenus", "scripting", "storage"].sort()
    );
    expect(manifest).not.toHaveProperty("content_scripts");
  });

  it("never grants global hosts at install time", async () => {
    const manifest = await loadManifest();
    expect(manifest.permissions).not.toContain("<all_urls>");
    expect(manifest.optional_host_permissions).not.toContain("<all_urls>");
    expect(manifest.optional_host_permissions).toContain("https://*/*");
  });

  it("does not allow remote extension scripts", async () => {
    const manifest = await loadManifest();
    expect(manifest.content_security_policy.extension_pages).toBe(
      "script-src 'self'; object-src 'self'"
    );
  });
});
