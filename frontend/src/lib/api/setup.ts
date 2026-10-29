import { getJson, postJson, HttpRequestError } from "./http";
import type { AccountStatus, SetupPlatformVerificationResult, SetupState } from "../types/common";

export async function loadSetupState(): Promise<SetupState> {
  // 保留迁移前直接 fetch 的语义：null 表示不设超时。
  return getJson("/api/setup/status", undefined, null, setupError("failed to load setup state"));
}

export async function postSetupLaunch(): Promise<{ ok: true; console_url: string } | { ok: false; error: string }> {
  // /api/setup/launch is fire-and-forget. It opens the dedicated Chrome
  // helper window but does not advance setup state.
  try {
    return await postJson("/api/setup/launch");
  } catch (error) {
    if (error instanceof HttpRequestError && error.kind === "http") {
      return { ok: false, error: `setup launch failed: ${error.status}` };
    }
    throw error;
  }
}

export async function postSetupVerifyExtension(): Promise<{
  ok: boolean;
  status?: "bound" | "no-connection" | "no-extension" | "opencli-missing" | "profile-use-failed";
  current_step?: string;
  context_id?: string | null;
  connected_profiles?: string[];
  hint?: string;
}> {
  return postJson("/api/setup/verify-extension", undefined, setupError(`verify extension failed`));
}

export async function postSetupVerifyPlatform(platform: string): Promise<SetupPlatformVerificationResult> {
  return postJson(`/api/setup/verify/${platform}`, undefined, setupError(`verify ${platform} failed`));
}

export async function postSetupSkipPlatform(platform: string): Promise<{ status: AccountStatus }> {
  return postJson(`/api/setup/skip/${platform}`, undefined, setupError(`skip ${platform} failed`));
}

export async function postSetupFirstFetch(): Promise<{
  ok: boolean;
  current_step: string;
  fetched_count: number;
  queued?: boolean;
}> {
  return postJson("/api/setup/first-fetch", undefined, setupError(`first fetch failed`));
}

export async function postSetupReset(): Promise<SetupState> {
  return postJson("/api/setup/reset", undefined, setupError(`setup reset failed`));
}

export async function postSetupDismiss(): Promise<SetupState> {
  return postJson("/api/setup/dismiss", undefined, setupError(`setup dismiss failed`));
}

function setupError(message: string) {
  return { httpError: (status: number) => new HttpRequestError("http", `${message}: ${status}`, status) };
}
