import { getJson } from "./http";
import type { CardPreviewFacets, CapturePreviewResolution, CardPreviewDetail, CardPreviewPage, CardPreviewQuery } from "../types/cardPreview";

export function fetchCardPreviewPage(
  filters: CardPreviewQuery = {},
  signal?: AbortSignal,
): Promise<CardPreviewPage> {
  const params = new URLSearchParams();
  const query = filters.query?.trim();
  if (query) params.set("query", query);
  if (filters.published_since) params.set("published_since", filters.published_since);
  if (filters.published_until) params.set("published_until", filters.published_until);
  if (filters.source_id) params.set("source_id", filters.source_id);
  if (filters.evaluation) params.set("evaluation", filters.evaluation);
  if (filters.platform) params.set("platform", filters.platform);
  if (filters.completeness) params.set("completeness", filters.completeness);
  if (filters.status) params.set("status", filters.status);
  if (filters.sort) params.set("sort", filters.sort);
  if (filters.digest !== undefined) params.set("digest", String(filters.digest));
  if (filters.recommended) params.set("recommended", "1");
  params.set("limit", String(filters.limit ?? 25));
  params.set("offset", String(filters.offset ?? 0));
  return getJson<CardPreviewPage>(`/api/card-preview?${params.toString()}`, signal);
}

export function fetchCardPreviewDetail(cardId: string, signal?: AbortSignal): Promise<CardPreviewDetail> {
  return getJson<CardPreviewDetail>(`/api/card-preview/${encodeURIComponent(cardId)}`, signal);
}

export function fetchCapturePreview(
  captureId: string,
  signal?: AbortSignal,
): Promise<CapturePreviewResolution> {
  return getJson<CapturePreviewResolution>(
    `/api/card-preview/captures/${encodeURIComponent(captureId)}`,
    signal,
  );
}

export function fetchCardPreviewFacets(
  signal?: AbortSignal,
  filters: Pick<CardPreviewQuery, "published_since" | "published_until"> & { scope?: "all" | "maintained" } = {},
): Promise<CardPreviewFacets> {
  const params = new URLSearchParams({ scope: filters.scope ?? "all" });
  if (filters.published_since) params.set("published_since", filters.published_since);
  if (filters.published_until) params.set("published_until", filters.published_until);
  return getJson<CardPreviewFacets>(`/api/card-preview/facets?${params}`, signal);
}
