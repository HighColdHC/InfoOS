import type { BrowserVisibility } from "../types/common";
import { getJson, sendEmpty, HttpRequestError } from "./http";

export function loadBrowserVisibility(): Promise<BrowserVisibility> {
  // 保留迁移前直接 fetch 的语义：null 表示不设超时。
  return getJson("/api/browser/visibility", undefined, null, {
    httpError: status => new HttpRequestError("http", `load visibility failed: ${status}`, status),
  });
}

export function saveBrowserVisibility(value: boolean): Promise<void> {
  return sendEmpty("/api/browser/visibility", "PUT", { headless_during_fetch: value }, {
    httpError: status => new HttpRequestError("http", `save visibility failed: ${status}`, status),
  });
}
