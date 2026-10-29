import { useCallback, useRef } from "react";
import { useCardFeedFilters } from "./useCardFeedFilters";
import { useCardFeedPagination } from "./useCardFeedPagination";
import { useCardFeedDetail } from "./useCardFeedDetail";

export function useCardFeed() {
  const initial = useRef(new URLSearchParams(window.location.search)).current;
  const filters = useCardFeedFilters(initial);
  const { itemsRef, resetKey, refreshVersion, ...list } = useCardFeedPagination(filters);
  const selectedItem = list.items.find((item) => item.card_id === filters.selectedId);
  const detail = useCardFeedDetail(initial, filters, resetKey, refreshVersion, selectedItem);
  const { setSelectedId, setMobileOpen, ...viewFilters } = filters;
  const select = useCallback((cardId: string, openMobile = true) => {
    const item = itemsRef.current.find((candidate) => candidate.card_id === cardId);
    if (item?.completeness_status === "processing") return;
    setSelectedId(cardId);
    if (openMobile) setMobileOpen(true);
  }, []);
  return { ...viewFilters, setMobileOpen, ...list, ...detail, select };
}
