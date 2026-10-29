import type { CaptureCapabilities, CaptureMode, ExtensionConfig } from "./types";

export const CONFIG_STORAGE_KEY = "saveToInfoosConfig";
export const LAST_OUTCOME_STORAGE_KEY = "saveToInfoosLastOutcome";

export const DEFAULT_CONFIG: ExtensionConfig = {
  baseUrl: "",
  token: "",
  defaultMode: "url_only",
  autoCloseAfterSuccess: false
};

const LOCAL_HTTP_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]", "::1"]);

export function normalizeInfoosBaseUrl(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) {
    throw new Error("missing_base_url");
  }

  let parsed: URL;
  try {
    parsed = new URL(trimmed);
  } catch {
    throw new Error("invalid_base_url");
  }

  if (parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new Error("unsafe_base_url");
  }

  const isHttps = parsed.protocol === "https:";
  const isLocalHttp =
    parsed.protocol === "http:" && LOCAL_HTTP_HOSTS.has(parsed.hostname);
  if (!isHttps && !isLocalHttp) {
    throw new Error("insecure_base_url");
  }

  const normalizedPath =
    parsed.pathname === "/" ? "" : parsed.pathname.replace(/\/+$/, "");
  return `${parsed.origin}${normalizedPath}`;
}

export function permissionPatternForBaseUrl(baseUrl: string): string {
  return `${new URL(baseUrl).origin}/*`;
}

export function apiUrl(baseUrl: string, path: string): string {
  const root = baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`;
  return new URL(path.replace(/^\/+/, ""), root).toString();
}

export async function loadConfig(): Promise<ExtensionConfig> {
  const stored = await chrome.storage.local.get(CONFIG_STORAGE_KEY);
  return {
    ...DEFAULT_CONFIG,
    ...(stored[CONFIG_STORAGE_KEY] as Partial<ExtensionConfig> | undefined)
  };
}

export async function saveConfig(config: ExtensionConfig): Promise<void> {
  await chrome.storage.local.set({ [CONFIG_STORAGE_KEY]: config });
}

export function isCaptureMode(value: unknown): value is CaptureMode {
  return value === "url_only" || value === "readable_content";
}

export function hasUsableCapabilities(
  capabilities: CaptureCapabilities | undefined
): capabilities is CaptureCapabilities {
  return Boolean(
    capabilities &&
      capabilities.interface_version === "capture.v1" &&
      capabilities.capabilities.includes("captures:write") &&
      capabilities.capabilities.includes("url_only") &&
      capabilities.limits.url_bytes > 0 &&
      capabilities.limits.title_bytes > 0 &&
      capabilities.limits.selected_text_bytes > 0 &&
      capabilities.limits.rendered_content_bytes > 0
  );
}
