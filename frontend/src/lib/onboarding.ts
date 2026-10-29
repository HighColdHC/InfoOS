const KEY = "infoos.onboarding.v1.completed";

export function isOnboardingComplete(): boolean {
  try {
    return window.localStorage.getItem(KEY) === "true";
  } catch {
    return false;
  }
}

export function markOnboardingComplete(): void {
  try {
    window.localStorage.setItem(KEY, "true");
  } catch {
    /* quota exceeded; best-effort */
  }
}
