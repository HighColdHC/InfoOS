export type TokenScope = "cards:read" | "assets:read" | "captures:write" | "intelligence:read" | "profile:write" | "evaluation:write" | "feedback:write" | "collect:write" | "sources:write" | "verification:write";

export type TokenOverview = {
  interface_path: string;
  interface_version: string;
  service_status: "ready" | "degraded";
  capabilities: TokenScope[];
};

export type TokenAdminRecord = {
  token_id: string;
  name: string;
  token_prefix: string;
  scopes: TokenScope[];
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
  replaced_by_token_id: string | null;
  status: "active" | "revoked" | "replaced" | (string & {});
  webhook_enabled: boolean;
  webhook_include_backfill?: boolean;
  webhook_url: string | null;
  webhook_config_generation: number;
  webhook_delivery_status: "inactive" | "pending_verification" | "active" | "paused" | (string & {});
  webhook_consecutive_failures: number;
  webhook_last_attempt_at: string | null;
  webhook_last_success_at: string | null;
  webhook_last_failure_at: string | null;
  webhook_last_error: string | null;
  webhook_circuit_opened_at: string | null;
  updated_at?: string | null;
};

export type TokenConfigurationInput = {
  name: string;
  scopes: TokenScope[];
  webhook_enabled: boolean;
  webhook_include_backfill?: boolean;
  webhook_url: string | null;
};

export type TokenCreateResult = {
  token: TokenAdminRecord;
  plaintext_token: string;
};

export type WebhookTestResult = {
  ok: true;
  status_code: number;
  token: TokenAdminRecord;
};

export type WebhookReplayResult = {
  ok: true;
  replayed_count: number;
  task_ids: string[];
};

export type RequestLogSummary = {
  request_count: number;
  success_2xx_count: number;
  client_error_4xx_count: number;
  median_duration_ms: number;
};

export type RequestLogItem = {
  request_id: string;
  created_at: string;
  method: string;
  path: string;
  status_code: number;
  result_code: string;
  result_summary: string;
  token_prefix: string | null;
  client_label: string | null;
  duration_ms: number;
};

export type RequestLogPage = {
  summary: RequestLogSummary;
  items: RequestLogItem[];
  total: number;
  next_offset: number | null;
};

export type RequestLogDetail = RequestLogItem & {
  response_meta: Record<string, unknown>;
};

export type RequestLogQuery = {
  query?: string;
  status?: string;
  time_range?: string;
  limit?: number;
  offset?: number;
};
