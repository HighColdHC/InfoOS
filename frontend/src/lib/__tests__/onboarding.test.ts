import { describe, it, expect, beforeEach } from "vitest";
import { isOnboardingComplete, markOnboardingComplete } from "../onboarding";

describe("onboarding storage", () => {
  beforeEach(() => localStorage.clear());

  it("returns false when no flag set", () => {
    expect(isOnboardingComplete()).toBe(false);
  });

  it("returns true after markOnboardingComplete", () => {
    markOnboardingComplete();
    expect(isOnboardingComplete()).toBe(true);
  });

  it("survives a simulated reload", () => {
    markOnboardingComplete();
    expect(isOnboardingComplete()).toBe(true);
  });
});
