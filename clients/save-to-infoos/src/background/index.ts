import { CaptureCoordinator } from "./coordinator";
import {
  activeTabId,
  currentConfig,
  getPageContext,
  getRenderedContent,
  loadLastOutcome,
  saveCapabilities,
  saveOutcome,
  loadPending,
  savePending
} from "./chrome-adapter";
import type {
  CaptureActionResult,
  CaptureMode,
  RuntimeRequest,
  RuntimeResponse
} from "../shared/types";

const MENU_PAGE = "save-to-infoos-page";
const MENU_SELECTION = "save-to-infoos-selection";

const coordinator = new CaptureCoordinator({
  loadConfig: currentConfig,
  saveCapabilities,
  saveOutcome,
  loadPending,
  savePending,
  getPageContext,
  getRenderedContent,
  clientVersion: chrome.runtime.getManifest().version
});

async function setBadge(state: "working" | "accepted" | "duplicate" | "error"): Promise<void> {
  const badge = {
    working: { text: "…", color: "#5f7567", title: "正在交给 InfoOS" },
    accepted: { text: "✓", color: "#245c3b", title: "InfoOS 已接收" },
    duplicate: { text: "=", color: "#50725d", title: "InfoOS 中已存在" },
    error: { text: "!", color: "#a34e3e", title: "未能交给 InfoOS" }
  }[state];
  await Promise.all([
    chrome.action.setBadgeText({ text: badge.text }),
    chrome.action.setBadgeBackgroundColor({ color: badge.color }),
    chrome.action.setTitle({ title: badge.title })
  ]);
}

async function captureWithBadge(
  tabId: number,
  mode: CaptureMode,
  selectedText?: string | null
): Promise<CaptureActionResult> {
  await setBadge("working");
  const result = await coordinator.capture(tabId, mode, selectedText);
  const badgeState =
    result.outcome.kind === "accepted"
      ? "accepted"
      : result.outcome.kind === "duplicate"
        ? "duplicate"
        : "error";
  await setBadge(badgeState);
  return result;
}

async function handleRequest(request: RuntimeRequest): Promise<RuntimeResponse> {
  if (request.type === "GET_ACTIVE_CONTEXT") {
    return { ok: true, context: await getPageContext(await activeTabId()) };
  }
  if (request.type === "GET_LAST_OUTCOME") {
    return { ok: true, outcome: await loadLastOutcome(), canRetry: await coordinator.restoreRetry() };
  }
  if (request.type === "CAPTURE_ACTIVE_TAB") {
    return {
      ok: true,
      ...(await captureWithBadge(await activeTabId(), request.mode))
    };
  }
  if (request.type === "RETRY_LAST_CAPTURE") {
    return { ok: true, ...(await coordinator.retry()) };
  }
  if (request.type === "DOWNGRADE_LAST_CAPTURE") {
    return { ok: true, ...(await coordinator.downgrade()) };
  }
  return { ok: false, error: "runtime_failure" };
}

chrome.runtime.onInstalled.addListener(() => {
  void chrome.contextMenus.removeAll().then(() => {
    chrome.contextMenus.create({
      id: MENU_PAGE,
      title: "交给 InfoOS",
      contexts: ["page"]
    });
    chrome.contextMenus.create({
      id: MENU_SELECTION,
      title: "把选中内容交给 InfoOS",
      contexts: ["selection"]
    });
  });
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (tab?.id === undefined) {
    return;
  }
  void currentConfig().then((config) =>
    captureWithBadge(
      tab.id as number,
      config.defaultMode,
      info.menuItemId === MENU_SELECTION ? info.selectionText : null
    )
  );
});

chrome.commands.onCommand.addListener((command) => {
  if (command !== "save-current-page") {
    return;
  }
  void Promise.all([activeTabId(), currentConfig()]).then(([tabId, config]) =>
    captureWithBadge(tabId, config.defaultMode)
  );
});

chrome.runtime.onMessage.addListener(
  (request: RuntimeRequest, _sender, sendResponse) => {
    void handleRequest(request)
      .then(sendResponse)
      .catch(() => sendResponse({ ok: false, error: "runtime_failure" }));
    return true;
  }
);
