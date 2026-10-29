import {
  CONFIG_STORAGE_KEY,
  LAST_OUTCOME_STORAGE_KEY,
  loadConfig,
  saveConfig
} from "../shared/config";
import type {
  CaptureCapabilities,
  CaptureOutcome,
  ExtensionConfig,
  PageContext,
  RenderedContent
} from "../shared/types";
import type { StoredPendingCapture } from "./coordinator";

const PENDING_CAPTURE_KEY = "infoos.pending-url-capture.v1";

export async function savePending(pending: StoredPendingCapture | null): Promise<void> {
  if (pending) await chrome.storage.local.set({ [PENDING_CAPTURE_KEY]: pending });
  else await chrome.storage.local.remove(PENDING_CAPTURE_KEY);
}

export async function loadPending(): Promise<StoredPendingCapture | null> {
  const stored = await chrome.storage.local.get(PENDING_CAPTURE_KEY);
  return stored[PENDING_CAPTURE_KEY] as StoredPendingCapture ?? null;
}

export async function saveCapabilities(
  capabilities: CaptureCapabilities,
  checkedAt: string
): Promise<void> {
  const config = await loadConfig();
  await saveConfig({
    ...config,
    capabilities,
    capabilitiesCheckedAt: checkedAt
  });
}

export async function saveOutcome(outcome: CaptureOutcome): Promise<void> {
  await chrome.storage.local.set({ [LAST_OUTCOME_STORAGE_KEY]: outcome });
}

export async function loadLastOutcome(): Promise<CaptureOutcome | null> {
  const stored = await chrome.storage.local.get(LAST_OUTCOME_STORAGE_KEY);
  return (stored[LAST_OUTCOME_STORAGE_KEY] as CaptureOutcome | undefined) ?? null;
}

export async function getPageContext(
  tabId: number,
  selectedTextOverride?: string | null
): Promise<PageContext> {
  const results = await chrome.scripting.executeScript({
    target: { tabId },
    func: () => {
      const canonicalElement = document.querySelector<HTMLLinkElement>(
        'link[rel~="canonical"]'
      );
      return {
        url: window.location.href,
        canonicalUrl: canonicalElement?.href ?? null,
        title: document.title || window.location.hostname,
        pageLanguage: document.documentElement.lang || null,
        selectedText: window.getSelection()?.toString().trim() || null
      };
    }
  });
  const context = results[0]?.result as PageContext | undefined;
  if (!context) {
    throw new Error("page_context_unavailable");
  }
  return {
    ...context,
    selectedText: selectedTextOverride?.trim() || context.selectedText
  };
}

export async function getRenderedContent(tabId: number): Promise<RenderedContent> {
  await chrome.scripting.executeScript({
    target: { tabId },
    files: ["content.js"]
  });
  const response = (await chrome.tabs.sendMessage(tabId, {
    type: "EXTRACT_READABLE_CONTENT"
  })) as
    | { ok: true; renderedContent: RenderedContent }
    | { ok: false; error: string };
  if (!response.ok) {
    throw new Error(response.error);
  }
  return response.renderedContent;
}

export async function activeTabId(): Promise<number> {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (tab?.id === undefined) {
    throw new Error("active_tab_unavailable");
  }
  return tab.id;
}

export async function currentConfig(): Promise<ExtensionConfig> {
  return loadConfig();
}

export async function clearStoredState(): Promise<void> {
  await chrome.storage.local.remove([CONFIG_STORAGE_KEY, LAST_OUTCOME_STORAGE_KEY]);
}
