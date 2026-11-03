import { getJson, sendJson } from "./http";
import type { BoardData, BoardFilters, ResearchStatus, ResearchStatusResult, StatsData, StatsWindow } from "../types/opportunities";

export function fetchOpportunityBoard(filters: BoardFilters, signal?: AbortSignal, status?: ResearchStatus, offset = 0) {
  const params = new URLSearchParams({ limit: "50", offset: String(offset) });
  for (const [key, value] of Object.entries(filters)) if (value.trim()) params.set(key, value.trim());
  if (status) params.set("status", status);
  return getJson<BoardData>(`/api/opportunities/board?${params}`, signal);
}
export function setResearchStatus(id: string, status: ResearchStatus, note: string | null = null) {
  return sendJson<ResearchStatusResult>(`/api/opportunities/${encodeURIComponent(id)}/research-status`, "PUT", { status, note });
}
export function fetchOpportunityStats(window: StatsWindow, signal?: AbortSignal) {
  return getJson<StatsData>(`/api/opportunities/stats?window=${window}`, signal);
}
