import { loadConfig } from "../shared/config";
import { MODE_HELP, outcomeCopy } from "../shared/strings";
import type {
  CaptureActionResult,
  CaptureMode,
  CaptureOutcome,
  PageContext,
  RuntimeRequest,
  RuntimeResponse
} from "../shared/types";

function element<T extends HTMLElement>(id: string): T {
  const found = document.getElementById(id);
  if (!found) {
    throw new Error(`missing_element:${id}`);
  }
  return found as T;
}

const pageTitle = element<HTMLParagraphElement>("page-title");
const pageOrigin = element<HTMLParagraphElement>("page-origin");
const captureMode = element<HTMLSelectElement>("capture-mode");
const modeHelp = element<HTMLParagraphElement>("mode-help");
const captureButton = element<HTMLButtonElement>("capture-button");
const statusCard = element<HTMLElement>("status-card");
const statusLabel = element<HTMLParagraphElement>("status-label");
const statusMessage = element<HTMLParagraphElement>("status-message");
const retryButton = element<HTMLButtonElement>("retry-button");
const downgradeButton = element<HTMLButtonElement>("downgrade-button");
const viewButton = element<HTMLButtonElement>("view-button");
const closeButton = element<HTMLButtonElement>("close-button");
const configurationHint = element<HTMLParagraphElement>("configuration-hint");
const openOptions = element<HTMLButtonElement>("open-options");

let activeViewUrl: string | undefined;
let autoCloseAfterSuccess = false;

async function send(request: RuntimeRequest): Promise<RuntimeResponse> {
  return chrome.runtime.sendMessage(request) as Promise<RuntimeResponse>;
}

function setBusy(busy: boolean): void {
  captureButton.disabled = busy;
  captureButton.textContent = busy ? "正在提交…" : "交给 InfoOS";
}

function toggle(button: HTMLElement, visible: boolean): void {
  button.classList.toggle("is-hidden", !visible);
}

function renderOutcome(
  outcome: CaptureOutcome,
  options: {
    canRetry?: boolean;
    canDowngrade?: boolean;
    allowAutoClose?: boolean;
  } = {}
): void {
  const copy = outcomeCopy(outcome);
  statusLabel.textContent = copy.label;
  statusMessage.textContent = outcome.title
    ? `${copy.message} 页面：${outcome.title}`
    : copy.message;
  statusCard.dataset.tone = copy.tone;
  statusCard.classList.remove("is-hidden");
  activeViewUrl = outcome.viewUrl;
  toggle(viewButton, Boolean(activeViewUrl));
  toggle(retryButton, Boolean(options.canRetry));
  toggle(downgradeButton, Boolean(options.canDowngrade));
  configurationHint.classList.toggle("is-hidden", outcome.kind !== "not_configured");

  if (
    options.allowAutoClose &&
    autoCloseAfterSuccess &&
    (outcome.kind === "accepted" || outcome.kind === "duplicate")
  ) {
    window.setTimeout(() => window.close(), 900);
  }
}

function renderRuntimeFailure(): void {
  renderOutcome({
    kind: "unreachable",
    title: pageTitle.textContent ?? "",
    mode: captureMode.value as CaptureMode,
    occurredAt: new Date().toISOString()
  });
}

async function performCapture(request: RuntimeRequest): Promise<void> {
  setBusy(true);
  statusCard.classList.add("is-hidden");
  try {
    const response = await send(request);
    if (!response.ok || !("outcome" in response) || !response.outcome) {
      renderRuntimeFailure();
      return;
    }
    const result = response as { ok: true } & CaptureActionResult;
    renderOutcome(result.outcome, { ...result, allowAutoClose: true });
  } catch {
    renderRuntimeFailure();
  } finally {
    setBusy(false);
  }
}

function renderContext(context: PageContext): void {
  pageTitle.textContent = context.title || "未命名页面";
  try {
    pageOrigin.textContent = new URL(context.url).origin;
  } catch {
    pageOrigin.textContent = "";
  }
}

captureMode.addEventListener("change", () => {
  modeHelp.textContent = MODE_HELP[captureMode.value as CaptureMode];
});

captureButton.addEventListener("click", () => {
  void performCapture({
    type: "CAPTURE_ACTIVE_TAB",
    mode: captureMode.value as CaptureMode
  });
});

retryButton.addEventListener("click", () => {
  void performCapture({ type: "RETRY_LAST_CAPTURE" });
});

downgradeButton.addEventListener("click", () => {
  captureMode.value = "url_only";
  modeHelp.textContent = MODE_HELP.url_only;
  void performCapture({ type: "DOWNGRADE_LAST_CAPTURE" });
});

viewButton.addEventListener("click", () => {
  if (activeViewUrl) {
    void chrome.tabs.create({ url: activeViewUrl });
  }
});

closeButton.addEventListener("click", () => window.close());
openOptions.addEventListener("click", () => chrome.runtime.openOptionsPage());

async function initialize(): Promise<void> {
  const config = await loadConfig();
  captureMode.value = config.defaultMode;
  modeHelp.textContent = MODE_HELP[config.defaultMode];
  autoCloseAfterSuccess = config.autoCloseAfterSuccess;
  const configured = Boolean(config.baseUrl && config.token);
  captureButton.disabled = !configured;
  configurationHint.classList.toggle("is-hidden", configured);

  const [contextResponse, outcomeResponse] = await Promise.all([
    send({ type: "GET_ACTIVE_CONTEXT" }).catch(() => null),
    send({ type: "GET_LAST_OUTCOME" }).catch(() => null)
  ]);

  if (contextResponse?.ok && "context" in contextResponse) {
    renderContext(contextResponse.context);
  } else {
    pageTitle.textContent = "当前页面不可读取";
    pageOrigin.textContent = "";
  }

  if (outcomeResponse?.ok && "outcome" in outcomeResponse && outcomeResponse.outcome) {
    renderOutcome(outcomeResponse.outcome, { allowAutoClose: false, canRetry: outcomeResponse.canRetry });
  }
}

void initialize();
