export type Stage = "inbox" | "plan" | "contact" | "action" | "archive";
export type FollowUpState = "following" | "dismissed" | "reset";
export type NextStep = "verify" | "observe" | "skip";
export type StatsWindow = "7d" | "30d" | "all";
export type CandidateKind = "prefilter" | "wind_vane" | "case";
export interface VerificationSummary {
  conclusion: "keep_observing" | "to_action" | "drop";
  credibility: string;
  contacted_on: string | null;
  doubts: string | null;
  created_at: string;
}
export interface OpportunitySource {
  author: string | null; platform: string; source_name: string | null;
  source_url: string; published_at: string | null; author_url: string | null;
}
export interface OpportunityItem extends OpportunitySource {
  candidate_kind: CandidateKind | null;
  card_id: string; title: string; offer: string; author: string | null;
  platform: string; platform_label: string; source_name: string | null;
  monetization_model: string; model_label: string; replicability: number; credibility: number;
  risk_flags: string[]; risk_labels: string[]; next_step: NextStep; question_count: number;
  created_at: string; stage: Stage; stage_since: string; stale: boolean;
  archive_reason: "dismissed" | "dropped" | null; latest_verification: VerificationSummary | null;
  verification_count: number; follow_up_reason: string | null;
}
export interface BoardData {
  columns: Record<Stage, { count: number; items: OpportunityItem[] }>;
  today: { inbox: number; plan_stale: number; contact: number; action: number };
  platforms: { platform: string; label: string }[];
  models: Record<string, string>;
}
export interface BoardFilters { query: string; platform: string; monetization_model: string; include_observe: boolean; candidate_kind: CandidateKind | "" }
export type OpportunityListItem = Omit<OpportunityItem, "stage" | "stage_since"> & { stage: Stage | null; stage_since: string | null };
export interface OpportunityPage {
  items: OpportunityListItem[]; total: number; limit: number; offset: number;
  platforms: BoardData["platforms"]; models: BoardData["models"];
}
export interface OpportunityListFilters { query: string; platform: string; monetization_model: string; next_step: string; stage: string; candidate_kind: CandidateKind | "" }
export interface OpportunityDetail extends OpportunitySource {
  candidate_kind: CandidateKind | null;
  card_id: string; title: string; offer: string; author: string | null; platform_label: string;
  stage: Stage | null; stage_since: string | null; stale: boolean;
  price_signal: string | null; funnel: string; target_customer: string | null;
  replicability: number; credibility: number; breakdown_note: string; created_at: string;
  evidence: string[]; verify_questions: string[]; risk_labels: string[];
  labels: { monetization_model: string; next_step: string };
  timeline: { id: string; kind: "breakdown" | "follow_up" | "verification"; created_at: string; summary: string; note: string | null }[];
}
export interface FollowUpResult { stage: Stage | null; stage_since: string | null; event_id: string | null }
export interface StatsData {
  window: StatsWindow; timezone: string;
  funnel: Record<"visible" | "checked" | "matched" | "broken_down" | "verify" | "following" | "contacted" | "action", number>;
  platforms: { platform: string; label: string; visible: number; matched: number; broken_down: number; verify: number }[];
  daily: { date: string; count: number }[];
  progress: { latest_breakdown_at: string | null; last_24h: number; pending: number };
  next_steps: Record<NextStep, number>;
  models: ({ model: string; label: string } & Record<NextStep, number>)[];
  points: [number, number, NextStep][];
  risks: { risk: string; label: string; count: number }[];
  follow_up: { stages: Record<Stage, number>; archive: { dismissed: number; dropped: number }; median_contact_days: number | null };
}
