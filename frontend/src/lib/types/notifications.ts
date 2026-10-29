export type FeishuSettings = {
  enabled: boolean;
  receive_id: string;
};
export type NotificationSettings = {
  config: {
    content_push: { daily_brief_digest: boolean };
    enabled: boolean;
    channels: { feishu: FeishuSettings };
    quiet_hours: { start: string; end: string; timezone: string };
    auth_qr: { codes_per_round: number; round_interval_hours: number; max_rounds_per_day: number };
    agent_watchdog: { evaluate_stall_hours: number; opportunities_stall_hours: number; evaluate_since: string; opportunities_since: string };
  };
  readiness: { feishu: boolean; email: boolean };
  latest_results: { channel: string; status: string; last_error_code: string | null; sent_at: string | null; updated_at: string }[];
};
export type NotificationPatch = {
  content_push?: NotificationSettings["config"]["content_push"];
  enabled?: boolean;
  channels?: { feishu: Partial<FeishuSettings> };
  quiet_hours?: NotificationSettings["config"]["quiet_hours"];
  auth_qr?: NotificationSettings["config"]["auth_qr"];
  agent_watchdog?: NotificationSettings["config"]["agent_watchdog"];
};
export type QrRoundResponse = { ok: boolean; round: { status: string; next_round_at?: string } };

export type FeishuConnection = {
  step: "create_app" | "authorize_user" | "done";
  status: "waiting_scan" | "completed" | "failed" | "idle";
  expires_at: string | null;
  error_code: string | null;
  qr_version: number;
};
