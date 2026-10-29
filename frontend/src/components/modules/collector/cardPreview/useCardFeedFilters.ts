import { useEffect, useState } from "react";
import type { CardPreviewSort } from "../../../../lib/types/cardPreview";

export function useCardFeedFilters(initial: URLSearchParams) {
  const initialStatus =
    initial.get("status") === "processing" || initial.get("completeness") === "processing"
      ? "processing"
      : initial.get("completeness") === "partial"
        ? "partial"
        : "";
  const [queryDraft, setQueryDraft] = useState(initial.get("q") || "");
  const [query, setQuery] = useState(initial.get("q") || "");
  const [platform, setPlatform] = useState(initial.get("platform") || "");
  const [sourceId, setSourceId] = useState(initial.get("source_id") || "");
  const [publishedSince, setPublishedSince] = useState(initial.get("published_since") || "");
  const [publishedUntil, setPublishedUntil] = useState(initial.get("published_until") || "");
  const [status, setStatus] = useState(initialStatus);
  const [recommended, setRecommended] = useState(initial.get("recommended") === "1");
  const [sort, setSort] = useState<CardPreviewSort>(
    initial.get("sort") === "updated_desc"
      ? "updated_desc"
      : initial.get("recommended") === "1" && initial.get("sort") !== "published_desc"
        ? "relevance_desc"
        : "published_desc",
  );
  const changeRecommended = (value: boolean) => {
    setRecommended(value);
    setSort((current) => (value ? "relevance_desc" : current === "relevance_desc" ? "published_desc" : current));
  };
  const [selectedId, setSelectedId] = useState<string | null>(initial.get("card_id"));
  const [mobileOpen, setMobileOpen] = useState(Boolean(initial.get("card_id") || initial.get("capture_id")));
  const [assetId, setAssetId] = useState<string | null>(initial.get("asset_id"));
  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(queryDraft.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [queryDraft]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    for (const key of [
      "q",
      "platform",
      "completeness",
      "status",
      "sort",
      "recommended",
      "card_id",
      "source_id",
      "published_since",
      "published_until",
    ])
      params.delete(key);
    if (sourceId) params.set("source_id", sourceId);
    if (publishedSince) params.set("published_since", publishedSince);
    if (publishedUntil) params.set("published_until", publishedUntil);
    if (query) params.set("q", query);
    if (platform) params.set("platform", platform);
    if (status === "partial") params.set("completeness", "partial");
    if (status === "processing") params.set("status", "processing");
    if (sort !== "published_desc") params.set("sort", sort);
    if (recommended) params.set("recommended", "1");
    if (selectedId) params.set("card_id", selectedId);
    const next = `${window.location.pathname}${params.size ? `?${params}` : ""}${window.location.hash}`;
    window.history.replaceState(window.history.state, "", next);
  }, [query, platform, sourceId, publishedSince, publishedUntil, status, sort, recommended, selectedId]);

  return {
    queryDraft,
    setQueryDraft,
    query,
    setQuery,
    platform,
    setPlatform,
    sourceId,
    setSourceId,
    publishedSince,
    setPublishedSince,
    publishedUntil,
    setPublishedUntil,
    status,
    setStatus,
    recommended,
    changeRecommended,
    sort,
    setSort,
    selectedId,
    setSelectedId,
    mobileOpen,
    setMobileOpen,
    assetId,
    setAssetId,
    changePlatform: (value: string) => {
      setPlatform(value);
      setSourceId("");
    },
    selectSource: (source: { source_id: string; platform: string }) => {
      setSourceId(source.source_id);
      setPlatform(source.platform);
    },
  };
}
