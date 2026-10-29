export type KnowledgeFilters = { kind: string; subtype: string; research_status: string; query: string };

export function readKnowledgeFilters(): KnowledgeFilters {
  const params = new URLSearchParams(window.location.search);
  return {
    kind: params.get("kind") || "", subtype: params.get("subtype") || "",
    research_status: params.get("research_status") || "", query: params.get("query") || "",
  };
}

export function knowledgeUrl(filters: KnowledgeFilters): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) if (value) params.set(key, value);
  return `/r/knowledge${params.size ? `?${params.toString()}` : ""}`;
}

export function selectKnowledgeKind(filters: KnowledgeFilters, kind: string): KnowledgeFilters {
  return { ...filters, kind, subtype: "", research_status: "" };
}
