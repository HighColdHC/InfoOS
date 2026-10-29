import { useEffect, useRef } from "react";
import type { CardPreviewListItem } from "../../../../lib/types/cardPreview";
import { CardFeedItem } from "./CardFeedItem";

type Props = {
  items: CardPreviewListItem[];
  selectedId: string | null;
  loading: boolean;
  error: boolean;
  hasMore: boolean;
  onSelect: (id: string) => void;
  onLoadMore: () => void;
  onRetry: () => void;
};
export function CardFeedList({ items, selectedId, loading, error, hasMore, onSelect, onLoadMore, onRetry }: Props) {
  const scroller = useRef<HTMLDivElement>(null);
  const sentinel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!hasMore || loading || error || !sentinel.current) return;
    const query = window.matchMedia("(max-width: 720px)");
    let observer: IntersectionObserver | undefined;
    const observe = () => {
      observer?.disconnect();
      observer = new IntersectionObserver(
        (entries) => { if (entries[0]?.isIntersecting) onLoadMore(); },
        { root: query.matches ? null : scroller.current, rootMargin: "200px" },
      );
      if (sentinel.current) observer.observe(sentinel.current);
    };
    observe();
    query.addEventListener("change", observe);
    return () => { observer?.disconnect(); query.removeEventListener("change", observe); };
  }, [hasMore, loading, error, onLoadMore]);
  useEffect(() => {
    if (!selectedId) return;
    const row = Array.from(scroller.current?.querySelectorAll<HTMLElement>("[data-card-id]") || []).find(
      (element) => element.dataset.cardId === selectedId,
    );
    row?.scrollIntoView({ block: "nearest" });
  }, [selectedId]);
  return (
    <div className="card-feed-list" ref={scroller} tabIndex={0} aria-label="信息卡列表">
      {items.map((item) => (
        <CardFeedItem
          key={item.card_id}
          item={item}
          selected={item.card_id === selectedId}
          onSelect={() => onSelect(item.card_id)}
        />
      ))}
      <div ref={sentinel} className="card-feed-list__sentinel" />
      {loading ? (
        <div className="card-feed-list__skeleton" role="status">
          <span />
          <span />
          <span />
        </div>
      ) : null}
      {error ? (
        <button type="button" className="card-feed-list__retry" onClick={onRetry}>
          加载失败，点击重试
        </button>
      ) : null}
    </div>
  );
}
