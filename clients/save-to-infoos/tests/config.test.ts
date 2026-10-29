import { describe, expect, it } from "vitest";
import {
  apiUrl,
  normalizeInfoosBaseUrl,
  permissionPatternForBaseUrl
} from "../src/shared/config";

describe("InfoOS address validation", () => {
  it("accepts HTTPS and removes trailing slashes", () => {
    expect(normalizeInfoosBaseUrl(" https://infoos.example.com/app/// ")).toBe(
      "https://infoos.example.com/app"
    );
  });

  it.each([
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://[::1]:8000"
  ])("accepts local HTTP for test tunnels: %s", (value) => {
    expect(normalizeInfoosBaseUrl(value)).toBe(value);
  });

  it.each([
    "http://infoos.example.com",
    "https://user:secret@infoos.example.com",
    "https://infoos.example.com?token=secret",
    "https://infoos.example.com/#capture"
  ])("rejects unsafe addresses: %s", (value) => {
    expect(() => normalizeInfoosBaseUrl(value)).toThrow();
  });

  it("requests only the configured origin and preserves a deployment prefix", () => {
    const baseUrl = "https://infoos.example.com/product";
    expect(permissionPatternForBaseUrl(baseUrl)).toBe(
      "https://infoos.example.com/*"
    );
    expect(apiUrl(baseUrl, "/api/capture/v1/items")).toBe(
      "https://infoos.example.com/product/api/capture/v1/items"
    );
  });
});
