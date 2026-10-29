import { getJson, sendJson } from "./http";
import type { OpportunityPage, OpportunityListFilters, BoardData, BoardFilters, FollowUpResult, FollowUpState, OpportunityDetail, StatsData, StatsWindow } from "../types/opportunities";

export function fetchOpportunityBoard(filters: BoardFilters, signal?: AbortSignal, limit = 20, offset = 0) {
  const params = new URLSearchParams({ include_observe: String(filters.include_observe), inbox_limit: String(limit), inbox_offset: String(offset) });
  if (filters.query.trim()) params.set("query", filters.query.trim());
  if (filters.platform) params.set("platform", filters.platform);
  if (filters.monetization_model) params.set("monetization_model", filters.monetization_model);
  if (filters.candidate_kind) params.set("candidate_kind", filters.candidate_kind);
  return getJson<BoardData>(`/api/opportunities/board?${params}`, signal);
}
export function fetchOpportunityDetail(id: string, signal?: AbortSignal) {
  return getJson<OpportunityDetail>(`/api/opportunities/${encodeURIComponent(id)}`, signal);
}
export function setOpportunityFollowUp(id: string, state: FollowUpState, reason: string | null = null) {
  return sendJson<FollowUpResult>(`/api/opportunities/${encodeURIComponent(id)}/follow-up`, "PUT", { state, reason });
}
export function fetchOpportunityStats(window: StatsWindow, signal?: AbortSignal) {
  return getJson<StatsData>(`/api/opportunities/stats?window=${window}`, signal);
}

export function fetchAllOpportunities(filters: OpportunityListFilters, offset: number, signal?: AbortSignal) {
  const params = new URLSearchParams({ limit: "20", offset: String(offset) });
  for (const [key, value] of Object.entries(filters)) if (value.trim()) params.set(key, value.trim());
  return getJson<OpportunityPage>(`/api/opportunities/all?${params}`, signal);
}
