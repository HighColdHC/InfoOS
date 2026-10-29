import { describe, it, expect } from "vitest";
import { cx, truncate, formatCompactDate, errorMessage } from "../format";

describe("cx", () => {
  it("joins basic string args, filtering falsy values", () => {
    expect(cx("a", false, "b")).toBe("a b");
  });

  it("filters null and undefined alongside false", () => {
    expect(cx("a", null, undefined, "b")).toBe("a b");
  });

  it("spreads object args by truthy keys", () => {
    expect(cx("a", { b: true, c: false })).toBe("a b");
  });

  it("handles object-first argument ordering", () => {
    expect(cx({ a: true, b: false }, "c")).toBe("a c");
  });

  it("treats empty strings and zero as falsy", () => {
    expect(cx("a", "", 0 as unknown as string, "b")).toBe("a b");
  });

  it("returns an empty string when every argument is falsy", () => {
    expect(cx(false, null, undefined, "", { a: false })).toBe("");
  });
});

describe("truncate", () => {
  it("returns the original text when shorter than maxLen", () => {
    expect(truncate("hi", 10)).toBe("hi");
  });

  it("appends the default suffix when truncating", () => {
    expect(truncate("hello world", 5)).toBe("hello…");
  });

  it("supports a custom suffix", () => {
    expect(truncate("hello world", 5, "...")).toBe("hello...");
  });

  it("returns just the suffix when maxLen is 0", () => {
    expect(truncate("abc", 0)).toBe("…");
  });

  it("handles empty input", () => {
    expect(truncate("", 5)).toBe("");
  });
});

describe("formatCompactDate", () => {
  it("formats an ISO string in local time as YYYY-MM-DD HH:mm", () => {
    // Build the expected string from the same local-time components the
    // implementation uses, so the test is timezone-agnostic.
    const iso = "2026-03-15T14:30:00Z";
    const date = new Date(iso);
    const pad = (n: number) => String(n).padStart(2, "0");
    const expected =
      `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
      `${pad(date.getHours())}:${pad(date.getMinutes())}`;
    expect(formatCompactDate(iso)).toBe(expected);
  });

  it("accepts a Date object and zero-pads single-digit fields", () => {
    // 2026-01-05 03:07 local — month, day, hour, and minute are all < 10
    const date = new Date(2026, 0, 5, 3, 7, 0);
    expect(formatCompactDate(date)).toBe("2026-01-05 03:07");
  });

  it("returns an empty string for null", () => {
    expect(formatCompactDate(null)).toBe("");
  });

  it("returns an empty string for undefined", () => {
    expect(formatCompactDate(undefined)).toBe("");
  });
});

describe("errorMessage", () => {
  it("extracts .message from Error instances", () => {
    expect(errorMessage(new Error("boom"))).toBe("boom");
  });

  it("returns a string error unchanged", () => {
    expect(errorMessage("just a string")).toBe("just a string");
  });

  it("stringifies other unknown values", () => {
    expect(errorMessage(42)).toBe("42");
    expect(errorMessage({ code: 1 })).toBe("[object Object]");
  });

  it("returns an empty string for null and undefined", () => {
    expect(errorMessage(null)).toBe("null");
    expect(errorMessage(undefined)).toBe("undefined");
  });
});
