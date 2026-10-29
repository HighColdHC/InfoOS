import { CaptureApiError, fetchCapabilities } from "../shared/api";
import {
  DEFAULT_CONFIG,
  loadConfig,
  normalizeInfoosBaseUrl,
  permissionPatternForBaseUrl,
  saveConfig
} from "../shared/config";
import type { CaptureMode } from "../shared/types";

function element<T extends HTMLElement>(id: string): T {
  const found = document.getElementById(id);
  if (!found) {
    throw new Error(`missing_element:${id}`);
  }
  return found as T;
}

const form = element<HTMLFormElement>("settings-form");
const baseUrlInput = element<HTMLInputElement>("base-url");
const tokenInput = element<HTMLInputElement>("token");
const tokenHint = element<HTMLElement>("token-hint");
const defaultModeInput = element<HTMLSelectElement>("default-mode");
const autoCloseInput = element<HTMLInputElement>("auto-close");
const saveButton = element<HTMLButtonElement>("save-button");
const clearTokenButton = element<HTMLButtonElement>("clear-token");
const connectionResult = element<HTMLElement>("connection-result");

let storedToken = "";
let storedBaseUrl = "";

function showResult(message: string, tone: "success" | "error"): void {
  connectionResult.textContent = message;
  connectionResult.dataset.tone = tone;
  connectionResult.classList.remove("is-hidden");
}

function safeConnectionError(error: unknown): string {
  if (error instanceof CaptureApiError) {
    const messages = {
      unauthorized: "Token 无效或已撤销。",
      forbidden: "Token 缺少 captures:write 权限。",
      too_large: "服务端拒绝了连接检查。",
      rejected: "InfoOS 拒绝了连接检查。",
      server_error: "InfoOS 暂时不可用。",
      timeout: "连接超时，请检查网络或隧道。",
      network: "无法连接 InfoOS。",
      invalid_response: "服务端不支持 capture.v1 合同。"
    };
    return messages[error.kind];
  }
  if (error instanceof Error) {
    const messages: Record<string, string> = {
      missing_base_url: "请填写 InfoOS 产品入口地址。",
      invalid_base_url: "InfoOS 地址格式无效。",
      unsafe_base_url: "地址不能包含用户名、密码、查询参数或锚点。",
      insecure_base_url: "正式连接必须使用 HTTPS；HTTP 只允许本机测试地址。"
    };
    return messages[error.message] ?? "设置保存失败。";
  }
  return "设置保存失败。";
}

async function initialize(): Promise<void> {
  const config = await loadConfig();
  storedToken = config.token;
  storedBaseUrl = config.baseUrl;
  baseUrlInput.value = config.baseUrl;
  defaultModeInput.value = config.defaultMode;
  autoCloseInput.checked = config.autoCloseAfterSuccess;
  if (storedToken) {
    tokenInput.placeholder = "已保存；留空保持不变";
    tokenHint.textContent = "已有捕获 Token。留空会保持原 Token，不会回填到页面。";
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  void (async () => {
    saveButton.disabled = true;
    saveButton.textContent = "正在测试…";
    connectionResult.classList.add("is-hidden");

    let newlyGrantedPattern: string | null = null;
    try {
      const baseUrl = normalizeInfoosBaseUrl(baseUrlInput.value);
      const token = tokenInput.value.trim() || storedToken;
      if (!token) {
        throw new Error("missing_token");
      }

      const permissionPattern = permissionPatternForBaseUrl(baseUrl);
      const alreadyGranted = await chrome.permissions.contains({
        origins: [permissionPattern]
      });
      if (
        !alreadyGranted &&
        !(await chrome.permissions.request({ origins: [permissionPattern] }))
      ) {
        throw new Error("host_permission_denied");
      }
      if (!alreadyGranted) {
        newlyGrantedPattern = permissionPattern;
      }

      const capabilities = await fetchCapabilities(baseUrl, token);
      if (
        defaultModeInput.value === "readable_content" &&
        !capabilities.capabilities.includes("readable_content")
      ) {
        throw new Error("readable_mode_unsupported");
      }
      const previousPattern = storedBaseUrl
        ? permissionPatternForBaseUrl(storedBaseUrl)
        : null;
      await saveConfig({
        baseUrl,
        token,
        defaultMode: defaultModeInput.value as CaptureMode,
        autoCloseAfterSuccess: autoCloseInput.checked,
        capabilities,
        capabilitiesCheckedAt: new Date().toISOString()
      });

      if (previousPattern && previousPattern !== permissionPattern) {
        await chrome.permissions.remove({ origins: [previousPattern] });
      }

      storedToken = token;
      storedBaseUrl = baseUrl;
      tokenInput.value = "";
      tokenInput.placeholder = "已保存；留空保持不变";
      tokenHint.textContent = "已有捕获 Token。留空会保持原 Token，不会回填到页面。";
      showResult(
        `连接成功：${capabilities.interface_version}；支持 ${capabilities.capabilities
          .filter((capability) => capability !== "captures:write")
          .join("、")}。`,
        "success"
      );
    } catch (error) {
      if (newlyGrantedPattern) {
        await chrome.permissions.remove({ origins: [newlyGrantedPattern] });
      }
      const localMessages: Record<string, string> = {
        missing_token: "请粘贴 captures:write Token。",
        host_permission_denied: "未获得该 InfoOS 地址的连接权限。",
        readable_mode_unsupported: "当前 InfoOS 不支持提交可读正文，请改用仅链接模式。"
      };
      const message =
        error instanceof Error && localMessages[error.message]
          ? localMessages[error.message]
          : safeConnectionError(error);
      showResult(message, "error");
    } finally {
      saveButton.disabled = false;
      saveButton.textContent = "保存并测试连接";
    }
  })();
});

clearTokenButton.addEventListener("click", () => {
  void (async () => {
    const config = await loadConfig();
    await saveConfig({
      ...config,
      token: "",
      capabilities: undefined,
      capabilitiesCheckedAt: undefined
    });
    storedToken = "";
    tokenInput.value = "";
    tokenInput.placeholder = "粘贴仅含 captures:write 的 Token";
    tokenHint.textContent = "Token 已清除。";
    showResult("捕获 Token 已清除。", "success");
  })();
});

void initialize();
