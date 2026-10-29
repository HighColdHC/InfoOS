import { getJson } from "./http";
import type { KnowledgeLibraryPage, KnowledgeLibraryQuery } from "../types/knowledge";

export function fetchKnowledgeLibrary(filters: KnowledgeLibraryQuery = {}, signal?: AbortSignal): Promise<KnowledgeLibraryPage> {
  const params = new URLSearchParams();
  for (const key of ["kind", "subtype", "research_status", "query", "cursor"] as const) {
    const value = filters[key];
    if (value) params.set(key, value);
  }
  if (filters.limit !== undefined) params.set("limit", String(filters.limit));
  return getJson<KnowledgeLibraryPage>(`/api/intelligence/knowledge?${params.toString()}`, signal);
}
