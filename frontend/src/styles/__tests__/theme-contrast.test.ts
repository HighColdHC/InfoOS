import { describe, it, expect } from "vitest";

// Manual contrast ratio verification. WCAG AA requires 4.5:1 for body
// text, 3:1 for large text. We assert the floor.
function srgbToLin(c: number): number {
  const cs = c / 255;
  return cs <= 0.03928 ? cs / 12.92 : Math.pow((cs + 0.055) / 1.055, 2.4);
}
function luminance(hex: string): number {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return 0.2126 * srgbToLin(r) + 0.7152 * srgbToLin(g) + 0.0722 * srgbToLin(b);
}
function contrast(a: string, b: string): number {
  const la = luminance(a);
  const lb = luminance(b);
  const [hi, lo] = la > lb ? [la, lb] : [lb, la];
  return (hi + 0.05) / (lo + 0.05);
}

describe("dark-mode contrast (WCAG AA)", () => {
  it("--color-info on dark bg is >= 4.5:1", () => {
    // After PR2: --color-info: #b0bcd0, --color-bg: #1a1816
    expect(contrast("#b0bcd0", "#1a1816")).toBeGreaterThanOrEqual(4.5);
  });
  it("--color-text on dark bg is >= 4.5:1", () => {
    // After PR2: --color-text: #f1ede6, --color-bg: #1a1816
    expect(contrast("#f1ede6", "#1a1816")).toBeGreaterThanOrEqual(4.5);
  });
  it("--color-text-muted on dark bg is >= 4.5:1", () => {
    // After PR2: --color-text-muted: #a89e93, --color-bg: #1a1816
    expect(contrast("#a89e93", "#1a1816")).toBeGreaterThanOrEqual(4.5);
  });
});
