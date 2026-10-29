import type { CardPreviewEvaluationView } from "../../lib/types/cardPreview";

export type BrowseFilters = {
  platform: string; source_id: string; evaluation: CardPreviewEvaluationView;
  sort: "published_desc" | "relevance_desc"; query: string;
};

export function readBrowseFilters(): BrowseFilters {
  const params = new URLSearchParams(window.location.search);
  const view = params.get("evaluation");
  const evaluation = view === "evaluated" || view === "unevaluated" ? view : "passed";
  return {
    platform: params.get("platform") || "", source_id: params.get("source_id") || "",
    evaluation, sort: evaluation === "passed" && params.get("sort") === "relevance_desc" ? "relevance_desc" : "published_desc",
    query: params.get("query") || "",
  };
}

export function browseUrl(filters: BrowseFilters): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) if (value) params.set(key, value);
  return `/r/browse?${params.toString()}`;
}
