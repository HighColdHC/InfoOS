import { useCallback, useEffect, useRef, useState } from "react";
import { isAbort } from "../../../../lib/isAbort";
import { fetchCardPreviewFacets, fetchCardPreviewPage } from "../../../../lib/api/cardPreview";
import type { CardPreviewFacets, CardPreviewListItem } from "../../../../lib/types/cardPreview";
import type { useCardFeedFilters } from "./useCardFeedFilters";

export function useCardFeedPagination(filters: ReturnType<typeof useCardFeedFilters>) {
  const {
    query,
    platform,
    sourceId,
    publishedSince,
    publishedUntil,
    status,
    sort,
    recommended,
    selectedId,
    setSelectedId,
    setMobileOpen,
  } = filters;
  const [items, setItems] = useState<CardPreviewListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [processingTotal, setProcessingTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [facets, setFacets] = useState<CardPreviewFacets | null>(null);
  const [facetsLoading, setFacetsLoading] = useState(true);
  const [facetsError, setFacetsError] = useState(false);
  const selectedRef = useRef(selectedId);
  selectedRef.current = selectedId;
  const itemsRef = useRef(items);
  itemsRef.current = items;
  const loadingRef = useRef(false);
  const generation = useRef(0);
  const mountedFilters = useRef(false);

  const load = useCallback(
    async (offset: number, currentGeneration: number, signal?: AbortSignal) => {
      if (loadingRef.current) return;
      loadingRef.current = true;
      setLoading(true);
      setError(false);
      try {
        const page = await fetchCardPreviewPage(
          {
            query,
            platform,
            source_id: sourceId,
            published_since: publishedSince,
            published_until: publishedUntil,
            completeness: status === "partial" ? "partial" : undefined,
            status: status === "processing" ? "processing" : undefined,
            recommended,
            sort,
            offset,
            limit: 30,
          },
          signal,
        );
        if (currentGeneration !== generation.current || signal?.aborted) return;
        setTotal(page.total);
        setProcessingTotal(page.processing_total);
        setItems((previous) => (offset === 0 ? page.items : [...previous, ...page.items]));
        if (offset === 0 && status !== "processing" && !selectedRef.current && page.items.length)
          setSelectedId(page.items[0].card_id);
      } catch (reason) {
        if (!isAbort(reason) && currentGeneration === generation.current) setError(true);
      } finally {
        if (currentGeneration === generation.current) {
          loadingRef.current = false;
          setLoading(false);
        }
      }
    },
    [query, platform, sourceId, publishedSince, publishedUntil, status, sort, recommended],
  );

  useEffect(() => {
    generation.current += 1;
    if (mountedFilters.current) setMobileOpen(false);
    mountedFilters.current = true;
    loadingRef.current = false;
    setItems([]);
    setTotal(0);
    setSelectedId(null);
    const controller = new AbortController();
    void load(0, generation.current, controller.signal);
    return () => controller.abort();
  }, [load, refresh]);

  useEffect(() => {
    const controller = new AbortController();
    setFacets(null);
    setFacetsLoading(true);
    setFacetsError(false);
    void fetchCardPreviewFacets(controller.signal, {
      published_since: publishedSince,
      published_until: publishedUntil,
    })
      .then((value) => {
        if (!controller.signal.aborted) setFacets(value);
      })
      .catch((reason) => {
        if (!isAbort(reason) && !controller.signal.aborted) setFacetsError(true);
      })
      .finally(() => {
        if (!controller.signal.aborted) setFacetsLoading(false);
      });
    return () => controller.abort();
  }, [publishedSince, publishedUntil, refresh]);

  const loadMore = useCallback(() => {
    if (!loadingRef.current && !error && itemsRef.current.length < total)
      void load(itemsRef.current.length, generation.current);
  }, [error, total, load]);
  const retry = useCallback(() => {
    if (loadingRef.current) return;
    if (itemsRef.current.length) void load(itemsRef.current.length, generation.current);
    else setRefresh((value) => value + 1);
  }, [load]);
  return {
    items,
    itemsRef,
    total,
    processingTotal,
    loading,
    error,
    facets,
    facetsLoading,
    facetsError,
    loadMore,
    retry,
    resetKey: load,
    refreshVersion: refresh,
    refresh: () => setRefresh((value) => value + 1),
  };
}
