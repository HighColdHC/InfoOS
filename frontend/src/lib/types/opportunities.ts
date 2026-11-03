export type ResearchStatus = "pending" | "researching" | "project" | "observe" | "drop";
export type StatsWindow = "7d" | "30d" | "all";
export interface OpportunityItem {
  knowledge_id: string; title: string; subtype: string; use_note: string; strength: number;
  research_status: ResearchStatus; status_changed_at: string; created_at: string; updated_at: string;
  evidence_count: number; platforms: string[]; origin_freshness: string; money_signal: string; signal_kinds: string[];
}
export interface BoardColumn {
  status: ResearchStatus; label: string; total: number; items: OpportunityItem[]; next_offset: number | null;
}
export interface BoardData { columns: BoardColumn[] }
export interface BoardFilters { query: string; platform: string; subtype: string }
export interface ResearchStatusResult {
  knowledge_id: string; research_status: ResearchStatus; changed: boolean; event_id?: string;
}
export interface StatsData {
  window: StatsWindow; total: number;
  by_status: { status: ResearchStatus; label: string; count: number }[];
  by_subtype: { subtype: string; label: string; count: number }[];
  by_signal: { signal: string; count: number }[];
  daily: { date: string; count: number }[];
  by_platform: { platform: string; label: string; count: number }[];
}
