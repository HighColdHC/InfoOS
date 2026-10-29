import { extractReadablePage } from "./extractor";

declare global {
  interface Window {
    __saveToInfoosContentScriptInstalled?: boolean;
  }
}

if (!window.__saveToInfoosContentScriptInstalled) {
  window.__saveToInfoosContentScriptInstalled = true;
  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type !== "EXTRACT_READABLE_CONTENT") {
      return false;
    }

    try {
      sendResponse({
        ok: true,
        renderedContent: extractReadablePage(document, window.location.href)
      });
    } catch {
      sendResponse({ ok: false, error: "readable_content_unavailable" });
    }
    return false;
  });
}
