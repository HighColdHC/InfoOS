import {
  CaptureApiError,
  fetchCapabilities,
  submitCapture
} from "../shared/api";
import { hasUsableCapabilities } from "../shared/config";
import {
  buildCaptureRequest,
  createClientCaptureId,
  PayloadValidationError
} from "../shared/payload";
import type {
  CaptureActionResult,
  CaptureCapabilities,
  CaptureMode,
  CaptureOutcome,
  CaptureRequest,
  CaptureReceipt,
  ExtensionConfig,
  PageContext,
  RenderedContent
} from "../shared/types";
import { resolveInfoosViewUrl } from "../shared/view-url";

interface CaptureDependencies {
  loadConfig: () => Promise<ExtensionConfig>;
  saveCapabilities: (
    capabilities: CaptureCapabilities,
    checkedAt: string
  ) => Promise<void>;
  saveOutcome: (outcome: CaptureOutcome) => Promise<void>;
  loadPending?: () => Promise<StoredPendingCapture | null>;
  savePending?: (pending: StoredPendingCapture | null) => Promise<void>;
  getPageContext: (
    tabId: number,
    selectedTextOverride?: string | null
  ) => Promise<PageContext>;
  getRenderedContent: (tabId: number) => Promise<RenderedContent>;
  fetchCapabilities?: typeof fetchCapabilities;
  submitCapture?: typeof submitCapture;
  now?: () => Date;
  createCaptureId?: () => string;
  clientVersion: string;
}

interface PendingCapture {
  baseUrl: string;
  token: string;
  request: CaptureRequest;
  title: string;
}

export interface StoredPendingCapture {
  baseUrl: string;
  tokenFingerprint: string;
  request: CaptureRequest;
  title: string;
  expiresAt: number;
}

async function tokenFingerprint(token: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(token));
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
}

interface DowngradeContext {
  context: PageContext;
  capabilities: CaptureCapabilities;
  config: ExtensionConfig;
}

const CAPABILITIES_TTL_MS = 10 * 60 * 1000;

function isFreshCapabilities(config: ExtensionConfig, now: Date): boolean {
  if (!hasUsableCapabilities(config.capabilities) || !config.capabilitiesCheckedAt) {
    return false;
  }
  const checkedAt = Date.parse(config.capabilitiesCheckedAt);
  return Number.isFinite(checkedAt) && now.getTime() - checkedAt < CAPABILITIES_TTL_MS;
}

function outcomeFromError(
  error: unknown,
  title: string,
  mode: CaptureMode,
  now: Date
): CaptureOutcome {
  const base = {
    title,
    mode,
    occurredAt: now.toISOString()
  };

  if (error instanceof PayloadValidationError) {
    return {
      ...base,
      kind: error.code === "content_too_large" ? "too_large" : "rejected",
      safeCode: error.code
    };
  }
  if (error instanceof CaptureApiError) {
    const kindMap = {
      unauthorized: "unauthorized",
      forbidden: "forbidden",
      too_large: "too_large",
      rejected: "rejected",
      server_error: "unconfirmed",
      timeout: "unconfirmed",
      network: "unreachable",
      invalid_response: "invalid_response"
    } as const;
    return {
      ...base,
      kind: kindMap[error.kind],
      safeCode: error.safeCode
    };
  }
  return {
    ...base,
    kind: "page_inaccessible"
  };
}

function successfulOutcome(
  receipt: CaptureReceipt,
  baseUrl: string,
  title: string,
  mode: CaptureMode,
  now: Date
): CaptureOutcome {
  return {
    kind: receipt.status === "duplicate" ? "duplicate" : "accepted",
    title,
    mode,
    occurredAt: now.toISOString(),
    viewUrl: resolveInfoosViewUrl(baseUrl, receipt.infoos_url),
    receipt: {
      captureId: receipt.capture_id,
      status: receipt.status,
      disposition: receipt.disposition,
      requestId: receipt.request_id
    }
  };
}

export class CaptureCoordinator {
  private pendingCapture: PendingCapture | null = null;
  private downgradeContext: DowngradeContext | null = null;

  constructor(private readonly dependencies: CaptureDependencies) {}

  async restoreRetry(): Promise<boolean> {
    if (this.pendingCapture) return true;
    const stored = await this.dependencies.loadPending?.();
    if (!stored) return false;
    const config = await this.dependencies.loadConfig();
    if (stored.expiresAt <= this.now().getTime() || stored.baseUrl !== config.baseUrl
        || stored.tokenFingerprint !== await tokenFingerprint(config.token)
        || stored.request.capture_mode !== "url_only" || stored.request.rendered_content || stored.request.selected_text) {
      await this.dependencies.savePending?.(null);
      return false;
    }
    this.pendingCapture = { baseUrl: stored.baseUrl, token: config.token, request: stored.request, title: stored.title };
    return true;
  }

  async capture(
    tabId: number,
    mode: CaptureMode,
    selectedTextOverride?: string | null
  ): Promise<CaptureActionResult> {
    const config = await this.dependencies.loadConfig();
    if (!config.baseUrl || !config.token) {
      return this.record({
        kind: "not_configured",
        title: "",
        mode,
        occurredAt: this.now().toISOString()
      });
    }

    let context: PageContext | null = null;
    try {
      const capabilities = await this.ensureCapabilities(config);
      context = await this.dependencies.getPageContext(tabId, selectedTextOverride);
      this.downgradeContext = {
        context,
        capabilities,
        config
      };
      const renderedContent =
        mode === "readable_content"
          ? await this.dependencies.getRenderedContent(tabId)
          : null;
      const request = buildCaptureRequest({
        context,
        mode,
        renderedContent,
        capabilities,
        clientCaptureId:
          this.dependencies.createCaptureId?.() ?? createClientCaptureId(),
        clientVersion: this.dependencies.clientVersion,
        capturedAt: this.now().toISOString()
      });
      return await this.submit(config, request, context.title);
    } catch (error) {
      const outcome = outcomeFromError(error, context?.title ?? "", mode, this.now());
      if (mode !== "readable_content" || outcome.kind !== "too_large") {
        this.downgradeContext = null;
      }
      return this.record(outcome);
    }
  }

  async retry(): Promise<CaptureActionResult> {
    await this.restoreRetry();
    if (!this.pendingCapture) {
      return this.record({
        kind: "unconfirmed",
        title: "",
        mode: "url_only",
        occurredAt: this.now().toISOString(),
        safeCode: "retry_expired"
      });
    }
    const pending = this.pendingCapture;
    return this.submit(
      { baseUrl: pending.baseUrl, token: pending.token },
      pending.request,
      pending.title
    );
  }

  async downgrade(): Promise<CaptureActionResult> {
    if (!this.downgradeContext) {
      return this.record({
        kind: "rejected",
        title: "",
        mode: "url_only",
        occurredAt: this.now().toISOString(),
        safeCode: "downgrade_expired"
      });
    }
    const { context, capabilities, config } = this.downgradeContext;
    const request = buildCaptureRequest({
      context,
      mode: "url_only",
      renderedContent: null,
      capabilities,
      clientCaptureId:
        this.dependencies.createCaptureId?.() ?? createClientCaptureId(),
      clientVersion: this.dependencies.clientVersion,
      capturedAt: this.now().toISOString()
    });
    return this.submit(config, request, context.title);
  }

  private now(): Date {
    return this.dependencies.now?.() ?? new Date();
  }

  private async ensureCapabilities(
    config: ExtensionConfig
  ): Promise<CaptureCapabilities> {
    const now = this.now();
    if (isFreshCapabilities(config, now) && config.capabilities) {
      return config.capabilities;
    }
    const capabilities = await (
      this.dependencies.fetchCapabilities ?? fetchCapabilities
    )(config.baseUrl, config.token);
    await this.dependencies.saveCapabilities(capabilities, now.toISOString());
    return capabilities;
  }

  private async submit(
    config: Pick<ExtensionConfig, "baseUrl" | "token">,
    request: CaptureRequest,
    title: string
  ): Promise<CaptureActionResult> {
    this.pendingCapture = {
      baseUrl: config.baseUrl,
      token: config.token,
      request,
      title
    };
    if (this.dependencies.savePending) {
      const safeToPersist = request.capture_mode === "url_only" && !request.rendered_content && !request.selected_text;
      await this.dependencies.savePending(safeToPersist ? {
        baseUrl: config.baseUrl, tokenFingerprint: await tokenFingerprint(config.token), request, title,
        expiresAt: this.now().getTime() + 30 * 60 * 1000,
      } : null);
    }
    try {
      const receipt = await (
        this.dependencies.submitCapture ?? submitCapture
      )(config.baseUrl, config.token, request);
      this.pendingCapture = null;
      await this.dependencies.savePending?.(null);
      this.downgradeContext = null;
      return this.record(
        successfulOutcome(
          receipt,
          config.baseUrl,
          title,
          request.capture_mode,
          this.now()
        )
      );
    } catch (error) {
      const outcome = outcomeFromError(
        error,
        title,
        request.capture_mode,
        this.now()
      );
      if (
        !(error instanceof CaptureApiError) ||
        (error.kind !== "timeout" &&
          error.kind !== "network" &&
          error.kind !== "server_error")
      ) {
        this.pendingCapture = null;
        await this.dependencies.savePending?.(null);
      }
      if (outcome.kind !== "too_large") {
        this.downgradeContext = null;
      }
      return this.record(outcome);
    }
  }

  private async record(outcome: CaptureOutcome): Promise<CaptureActionResult> {
    await this.dependencies.saveOutcome(outcome);
    return {
      outcome,
      canRetry: Boolean(this.pendingCapture),
      canDowngrade:
        Boolean(this.downgradeContext) &&
        outcome.mode === "readable_content" &&
        outcome.kind === "too_large"
    };
  }
}
