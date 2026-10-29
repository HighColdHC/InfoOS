export type AuthRecoveryStatus = "awaiting_user" | "verifying" | "resolved";

export type AuthRecoveryIncident = {
  incident_id: string;
  platform: string;
  platform_label: string;
  status: AuthRecoveryStatus;
  reason_code: string;
  created_at: string;
  updated_at: string;
  resolved_at: string | null;
  affected_task_count: number;
  recovered_task_count: number;
  last_error_code: string | null;
};

export type AuthRecoveryVerification = {
  status: "passed" | "failed";
  checked_at: string;
  method?: string;
  code?: string;
};

export type AuthRecoveryResponse = {
  ok: true;
  incident: AuthRecoveryIncident;
};

export type OpenAuthRecoveryConsoleResponse = AuthRecoveryResponse & {
  console_url: string;
};

export type VerifyAuthRecoveryResponse = {
  ok: boolean;
  verification: AuthRecoveryVerification;
  incident: AuthRecoveryIncident;
};
