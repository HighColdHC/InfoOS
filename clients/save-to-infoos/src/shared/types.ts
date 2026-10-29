export type CaptureMode = "url_only" | "readable_content";

export interface CaptureLimits {
  url_bytes: number;
  title_bytes: number;
  selected_text_bytes: number;
  rendered_content_bytes: number;
}

export interface CaptureCapabilities {
  interface_version: string;
  capabilities: string[];
  limits: CaptureLimits;
  accepted_content_formats: string[];
}

export interface ExtensionConfig {
  baseUrl: string;
  token: string;
  defaultMode: CaptureMode;
  autoCloseAfterSuccess: boolean;
  capabilities?: CaptureCapabilities;
  capabilitiesCheckedAt?: string;
}

export interface PageContext {
  url: string;
  canonicalUrl: string | null;
  title: string;
  pageLanguage: string | null;
  selectedText: string | null;
}

export interface RenderedContent {
  format: "readability_html";
  html: string;
  text: string;
  byline?: string;
  excerpt?: string;
  site_name?: string;
}

export interface CaptureRequest {
  schema: "infoos.capture-request.v1";
  client_capture_id: string;
  url: string;
  canonical_url: string | null;
  title: string;
  selected_text: string | null;
  page_language: string | null;
  captured_at: string;
  capture_mode: CaptureMode;
  rendered_content: RenderedContent | null;
  client: {
    name: "save-to-infoos";
    version: string;
  };
}

export type ReceiptStatus = "accepted" | "duplicate" | "rejected";
export type ReceiptDisposition =
  | "new"
  | "refresh"
  | "unchanged"
  | "idempotent_replay";

export interface CaptureReceipt {
  schema: "infoos.capture-receipt.v1";
  capture_id: string;
  status: ReceiptStatus;
  disposition: ReceiptDisposition;
  raw_item_id?: string | null;
  task_id?: string | null;
  card_id?: string | null;
  infoos_url: string;
  request_id: string;
}

export type OutcomeKind =
  | "accepted"
  | "duplicate"
  | "unconfirmed"
  | "rejected"
  | "unauthorized"
  | "forbidden"
  | "too_large"
  | "unreachable"
  | "not_configured"
  | "page_inaccessible"
  | "invalid_response";

export interface CaptureOutcome {
  kind: OutcomeKind;
  title: string;
  mode: CaptureMode;
  occurredAt: string;
  safeCode?: string;
  viewUrl?: string;
  receipt?: {
    captureId: string;
    status: ReceiptStatus;
    disposition: ReceiptDisposition;
    requestId: string;
  };
}

export interface CaptureActionResult {
  outcome: CaptureOutcome;
  canRetry: boolean;
  canDowngrade: boolean;
}

export type RuntimeRequest =
  | { type: "GET_ACTIVE_CONTEXT" }
  | { type: "GET_LAST_OUTCOME" }
  | { type: "CAPTURE_ACTIVE_TAB"; mode: CaptureMode }
  | { type: "RETRY_LAST_CAPTURE" }
  | { type: "DOWNGRADE_LAST_CAPTURE" };

export type RuntimeResponse =
  | { ok: true; context: PageContext }
  | { ok: true; outcome: CaptureOutcome | null; canRetry?: boolean }
  | ({ ok: true } & CaptureActionResult)
  | { ok: false; error: "runtime_failure" };
