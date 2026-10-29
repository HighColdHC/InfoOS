import { useEffect, useRef, useState } from "react";
import { isAbort } from "../../../../lib/isAbort";
import { fetchCapturePreview, fetchCardPreviewDetail } from "../../../../lib/api/cardPreview";
import type { CardPreviewDetail, CardPreviewListItem } from "../../../../lib/types/cardPreview";
import type { useCardFeedFilters } from "./useCardFeedFilters";

export function useCardFeedDetail(
  initial: URLSearchParams,
  filters: ReturnType<typeof useCardFeedFilters>,
  resetKey: unknown,
  refreshVersion: number,
  selectedItem: CardPreviewListItem | undefined,
) {
  const { selectedId, status, setSelectedId, setMobileOpen } = filters;
  const [detail, setDetail] = useState<CardPreviewDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState(false);
  const [deepLinkMessage, setDeepLinkMessage] = useState<string | null>(null);
  const cache = useRef(new Map<string, CardPreviewDetail>());
  const selectedVersion = selectedItem?.version;
  const selectedUpdatedAt = selectedItem?.updated_at;
  useEffect(() => {
    cache.current.clear();
  }, [refreshVersion]);
  useEffect(() => {
    setDetail(null);
  }, [resetKey, refreshVersion]);
  useEffect(() => {
    if (!selectedId || status === "processing") {
      setDetail(null);
      setDetailLoading(false);
      return;
    }
    const cached = cache.current.get(selectedId);
    cache.current.delete(selectedId);
    if (cached && (selectedVersion === undefined || (
      cached.card.version === selectedVersion && cached.card.updated_at === selectedUpdatedAt
    ))) {
      cache.current.set(selectedId, cached);
      setDetail(cached);
      setDetailLoading(false);
      setDetailError(false);
      return;
    }
    const controller = new AbortController();
    setDetail(null);
    setDetailLoading(true);
    setDetailError(false);
    void fetchCardPreviewDetail(selectedId, controller.signal)
      .then((value) => {
        if (controller.signal.aborted) return;
        cache.current.set(selectedId, value);
        if (cache.current.size > 50) cache.current.delete(cache.current.keys().next().value!);
        setDetail(value);
      })
      .catch((reason) => {
        if (!controller.signal.aborted && !isAbort(reason)) setDetailError(true);
      })
      .finally(() => {
        if (!controller.signal.aborted) setDetailLoading(false);
      });
    return () => controller.abort();
  }, [selectedId, status, selectedVersion, selectedUpdatedAt, refreshVersion]);

  useEffect(() => {
    const captureId = initial.get("capture_id");
    const cardId = initial.get("card_id");
    if (!captureId && !cardId) return;
    const controller = new AbortController();
    if (cardId) {
      setSelectedId(cardId);
      return () => controller.abort();
    }
    void fetchCapturePreview(captureId!, controller.signal)
      .then((resolution) => {
        if (resolution.card_id) {
          setSelectedId(resolution.card_id);
          setMobileOpen(true);
        } else
          setDeepLinkMessage(
            resolution.processing_status === "failed"
              ? "这次捕获处理失败，可到任务模块查看。"
              : "这次捕获仍在处理中，可稍后刷新。 ",
          );
      })
      .catch((reason) => {
        if (!isAbort(reason)) setDeepLinkMessage("无法定位指定的捕获记录。");
      });
    return () => controller.abort();
  }, []);

  return {
    detail,
    detailLoading,
    detailError,
    deepLinkMessage,
    setDeepLinkMessage,
  };
}
