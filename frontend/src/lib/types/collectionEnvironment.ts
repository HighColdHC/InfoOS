export type EnvironmentComponentStatus = "installed" | "configured" | "missing" | "unknown" | "failed" | string;
export type PlatformAccountStatus = "verified" | "needs_login" | "unknown" | "public" | "failed" | string;

export type EnvironmentComponent = {
  id: string;
  name: string;
  status: EnvironmentComponentStatus;
  version: string | null;
  is_chrome: boolean;
  action: string | null;
};

export type PlatformAccount = {
  platform: string;
  label: string;
  status: PlatformAccountStatus;
  browser_capability: string;
  session_id: string | null;
  last_checked_at: string | null;
  affected_source_count: number;
  action_hint: string | null;
  error?: string | null;
};

export type RuntimeCircuit = {
  scope_kind: "platform" | "provider";
  scope_key: string;
  source_id?: string;
  source_name?: string;
  state: "closed" | "open" | "half_open";
  consecutive_failures: number;
  reason_code: string | null;
  opened_at: string | null;
  next_probe_at: string | null;
  last_failure_at: string | null;
  last_success_at: string | null;
  updated_at: string;
  user_action: "none" | "login_required" | "config_required" | "restore_provider_quota";
};

export type AuthRecoverySummary = {
  incident_id: string;
  platform: string;
  status: "awaiting_user" | "verifying";
  reason_code: string;
  affected_task_count: number;
  created_at: string;
  updated_at: string;
  last_error_code: string | null;
  open_console_url: string;
  verify_url: string;
};

export type CollectionEnvironmentSnapshot = {
  runtime_health?: {
    status: "healthy" | "unhealthy" | "unknown" | string;
    reasons: string[];
    checked_at: string | null;
  };
  runtime_circuits: RuntimeCircuit[];
  pending_auth_recoveries: AuthRecoverySummary[];
  components: EnvironmentComponent[];
  accounts: PlatformAccount[];
  snapshot_at: string | null;
};

export type BrowserActionResult = {
  ok: boolean;
  platform?: string;
  task_id?: string;
  start_url?: string;
  status?: string;
};

// Only launch actions return a console URL; passive login checks do not.
export type BrowserConsoleActionResult = BrowserActionResult & { console_url: string };
