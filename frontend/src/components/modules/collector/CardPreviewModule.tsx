import "../../../styles/_collector-cards.css";
import "../../../styles/_card-feed.css";
import { Button } from "../../ui/primitives/Button";
import { useCallback, useEffect, useState } from "react";
import type { CardPreviewAsset, CardPreviewDetail as CardPreviewDetailType } from "../../../lib/types/cardPreview";
import { collectorPlatformOptions } from "../../../lib/platforms";
import { CardSourceFilters } from "./cardPreview/CardSourceFilters";
import { CardPreviewToolbar } from "./cardPreview/CardPreviewToolbar";
import { CardFeedList } from "./cardPreview/CardFeedList";
import { CardPreviewDetail } from "./cardPreview/CardPreviewDetail";
import { CardReaderModal } from "../../reader/CardReaderModal";
import { MediaPreviewModal } from "./cardPreview/MediaPreviewModal";
import { useCardFeed } from "./cardPreview/useCardFeed";
import { useHotkeys } from "../../ui/hooks/useHotkeys";
import { navigateToMenu } from "../../../lib/navigation";

type MediaPreviewState = { assets: CardPreviewAsset[]; index: number };
export function CardPreviewModule() {
  const feed = useCardFeed();
  const [readerDetail, setReaderDetail] = useState<CardPreviewDetailType | null>(null);
  const closeReader = useCallback(() => setReaderDetail(null), []);
  const [mediaPreview, setMediaPreview] = useState<MediaPreviewState | null>(null);
  useEffect(() => {
    if (!feed.assetId || !feed.detail) return;
    const assets = [...feed.detail.image_assets, ...feed.detail.video_assets];
    const index = assets.findIndex(
      (asset) => asset.asset_id === feed.assetId && asset.card_id === feed.detail?.card.card_id,
    );
    if (index >= 0) {
      setMediaPreview({ assets, index });
      feed.setDeepLinkMessage(null);
    } else feed.setDeepLinkMessage("指定媒体不存在、不可交付，或不属于当前信息卡。");
    feed.setAssetId(null);
  }, [feed.assetId, feed.detail]);
  const pinned =
    feed.detail && !feed.items.some((item) => item.card_id === feed.detail?.card.card_id)
      ? [
          {
            ...feed.detail.card,
            excerpt: feed.detail.body_blocks.find((block) => block.layer === "original")?.content ?? null,
            summary_excerpt:
              feed.detail.body_blocks.find((block) => block.layer === "intelligence" && block.block_kind === "summary")
                ?.content ?? null,
            cover_asset_url: feed.detail.image_assets.at(0)?.asset_url ?? feed.detail.video_assets.at(0)?.asset_url ?? null,
            cover_kind: feed.detail.image_assets.length
              ? ("image" as const)
              : feed.detail.video_assets.length
                ? ("video" as const)
                : null,
            media_count: feed.detail.image_assets.length + feed.detail.video_assets.length,
            attachment_kinds: [
              ...new Set([...feed.detail.image_assets, ...feed.detail.video_assets].map((asset) => asset.asset_kind)),
            ],
            evaluation: null,
          },
        ]
      : [];
  const visibleItems = [...pinned, ...feed.items];
  const modalOpen = () =>
    Boolean(mediaPreview) || Boolean(document.querySelector('[role="dialog"], [aria-modal="true"]'));
  const move = (direction: number) => {
    if (modalOpen()) return;
    const index = visibleItems.findIndex((item) => item.card_id === feed.selectedId);
    const candidates =
      direction > 0 ? visibleItems.slice(index + 1) : visibleItems.slice(0, Math.max(index, 0)).reverse();
    const next = candidates.find((item) => item.completeness_status !== "processing");
    if (next) feed.select(next.card_id, false);
    else if (direction > 0 && feed.items.length < feed.total) feed.loadMore();
  };
  const openSource = () => {
    if (modalOpen()) return;
    const url =
      feed.detail?.card.source_url || visibleItems.find((item) => item.card_id === feed.selectedId)?.source_url;
    if (url) window.open(url, "_blank", "noopener,noreferrer");
  };
  useHotkeys(
    {
      j: () => move(1),
      ArrowDown: () => move(1),
      k: () => move(-1),
      ArrowUp: () => move(-1),
      "/": () => {
        if (!modalOpen())
          document.querySelector<HTMLInputElement>('.card-reader-toolbar input[type="search"]')?.focus();
      },
      r: () => { if (feed.detail) setReaderDetail(feed.detail); },
      o: openSource,
      Enter: openSource,
    },
    { shouldHandle: () => !modalOpen() },
  );
  return (
    <section className="card-reader" aria-label="卡片阅读器" data-message={feed.deepLinkMessage ? "true" : undefined}>
      <CardPreviewToolbar
        filtersActive={Number(Boolean(feed.publishedSince)) + Number(Boolean(feed.publishedUntil)) + Number(Boolean(feed.sourceId))}
        queryDraft={feed.queryDraft}
        query={feed.query}
        platform={feed.platform}
        status={feed.status}
        sort={feed.sort}
        recommended={feed.recommended}
        total={feed.total}
        processingTotal={feed.processingTotal}
        loading={feed.loading}
        platformOptions={collectorPlatformOptions(feed.items.map((item) => item.platform))}
        onQueryDraftChange={feed.setQueryDraft}
        onSearch={() => feed.setQuery(feed.queryDraft.trim())}
        onPlatformChange={feed.changePlatform}
        onStatusChange={feed.setStatus}
        onSortChange={feed.setSort}
        onRecommendedChange={feed.changeRecommended}
        onRefresh={feed.refresh}
        onResearch={() => navigateToMenu("tasks", { tab: "research", keywords: feed.query })}
      >
        <CardSourceFilters
          publishedSince={feed.publishedSince} publishedUntil={feed.publishedUntil}
          onSinceChange={feed.setPublishedSince} onUntilChange={feed.setPublishedUntil}
          sourceId={feed.sourceId} platform={feed.platform} onSelect={feed.selectSource}
          onClear={() => feed.setSourceId("")} facets={feed.facets}
          loading={feed.facetsLoading} error={feed.facetsError} onRetry={feed.refresh}
        />
      </CardPreviewToolbar>
      {feed.deepLinkMessage ? (
        <div className="card-reader__message" role="status">
          {feed.deepLinkMessage}
        </div>
      ) : null}
      <div className="card-reader__panes" data-mobile-open={feed.mobileOpen || undefined}>
        <div className="card-reader__list-pane">
          {!feed.loading && !feed.error && !visibleItems.length ? (
            <div className="card-reader__empty">
              <p>没有符合条件的信息卡</p>
              {feed.query ? (
                <Button
                  type="button"
                  variant="primary"
                  size="sm"
                  onClick={() => navigateToMenu("tasks", { tab: "research", keywords: feed.query })}
                >
                  去平台调研“{feed.query}”
                </Button>
              ) : null}
            </div>
          ) : null}
          <CardFeedList
            items={visibleItems}
            selectedId={feed.selectedId}
            loading={feed.loading}
            error={feed.error}
            hasMore={feed.items.length < feed.total}
            onSelect={feed.select}
            onLoadMore={feed.loadMore}
            onRetry={feed.retry}
          />
        </div>
        <div className="card-reader__detail-pane">
          <button type="button" className="card-reader__back" onClick={() => feed.setMobileOpen(false)}>
            返回
          </button>
          {feed.status === "processing" ? (
            <p className="card-reader__empty">处理中卡片等待定稿</p>
          ) : feed.detailLoading ? (
            <p className="card-reader__empty">正在读取信息卡…</p>
          ) : feed.detailError ? (
            <p className="card-reader__empty">详情加载失败，请重新选择卡片</p>
          ) : feed.detail ? (
            <CardPreviewDetail detail={feed.detail} onRead={() => setReaderDetail(feed.detail)} onPreview={(assets, index) => setMediaPreview({ assets, index })} />
          ) : (
            <p className="card-reader__empty">选择一张信息卡开始阅读</p>
          )}
        </div>
      </div>
      <CardReaderModal target={readerDetail ? { cardId: readerDetail.card.card_id, detail: readerDetail } : null} onClose={closeReader} />
      <MediaPreviewModal
        assets={mediaPreview?.assets ?? []}
        index={mediaPreview?.index ?? 0}
        onIndexChange={(index) => setMediaPreview((current) => (current ? { ...current, index } : null))}
        onClose={() => setMediaPreview(null)}
      />
    </section>
  );
}
