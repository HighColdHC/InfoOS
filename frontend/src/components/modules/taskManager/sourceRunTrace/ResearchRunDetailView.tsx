import { Button } from "../../../ui/primitives/Button";
import { CardPreviewDetail } from "../../collector/cardPreview/CardPreviewDetail";
import type { CardPreviewAsset, CardPreviewDetail as CardPreviewDetailType } from "../../../../lib/types/cardPreview";
import type { SourceRunTrace, SourceRunTraceItem } from "../../../../lib/types/sourceRuns";

type Props = {
  trace: SourceRunTrace;
  detail: CardPreviewDetailType | null;
  detailLoading: boolean;
  detailError: string | null;
  onPreviewCard: (item: SourceRunTraceItem) => void;
  onPreviewMedia: (assets: CardPreviewAsset[], index: number) => void;
  onRead: (detail: CardPreviewDetailType) => void;
  onBack: () => void;
  onLoadMore: () => void;
  loadingMore: boolean;
};

function count(value: number | null): string {
  return value === null ? "—" : String(value);
}

function cardIdOf(item: SourceRunTraceItem): string | null {
  const cardId = item.card?.id ?? item.card_id ?? null;
  return cardId && cardId !== "unavailable" ? cardId : null;
}

function previewableCardId(item: SourceRunTraceItem): string | null {
  const cardId = cardIdOf(item);
  return cardId &&
    item.finalize.status === "success" &&
    (item.finalize.completeness === "complete" || item.finalize.completeness === "partial")
    ? cardId
    : null;
}

function associationLabel(item: SourceRunTraceItem): string | null {
  const value = item.card_projection_provenance ?? item.association;
  if (value === "created_or_updated_by_this_pipeline") return "本次运行创建或更新";
  if (value === "referenced_existing_pipeline" || value === "reused_existing_pipeline") return "复用已有信息卡链路";
  if (value === "provenance_unavailable") return "关联证据不可用";
  return value ? "已关联本次运行" : null;
}

export function ResearchRunDetailView({
  trace,
  detail,
  detailLoading,
  detailError,
  onPreviewCard,
  onPreviewMedia,
  onRead,
  onBack,
  onLoadMore,
  loadingMore,
}: Props) {
  if (detail || detailLoading || detailError) {
    return (
      <section className="collector-task-research-detail" aria-label="信息卡详情">
        <Button variant="secondary" size="sm" type="button" onClick={onBack}>
          返回调研详情
        </Button>
        {detailLoading ? (
          <div className="collector-task-state" role="status">
            正在读取信息卡详情…
          </div>
        ) : null}
        {detailError ? (
          <div className="collector-task-state is-error" role="alert">
            信息卡读取失败：{detailError}
          </div>
        ) : null}
        {detail ? <CardPreviewDetail detail={detail} onPreview={onPreviewMedia} onRead={() => onRead(detail)} /> : null}
      </section>
    );
  }

  const progress = trace.run.card_progress;
  const completed = progress.complete;
  const partial = progress.partial;
  const failed = progress.failed;
  const total = progress.total;
  const progressText =
    total !== null && completed !== null && partial !== null && failed !== null
      ? `${completed + partial + failed} / ${total}`
      : "不可用";
  const processed =
    total !== null && completed !== null && partial !== null && failed !== null ? completed + partial + failed : null;
  const progressPercent =
    processed !== null && total !== null
      ? total === 0
        ? 100
        : Math.min(100, Math.round((processed / total) * 100))
      : null;
  const items = trace.items;
  const itemTotal = trace.item_page.item_total;
  return (
    <section className="collector-task-research-detail" aria-label="调研详情">
      <div className="collector-task-trace-topstats">
        <div>
          <h3>本次检索语料</h3>
          <p>{count(trace.discovery.total_links)} 条实际返回结果；结果受本次检索 limit 限制。</p>
        </div>
        <div>
          <h3>卡片进度</h3>
          <p>
            {progressText}
            {progressPercent === null ? "" : ` · ${progressPercent}%`} · 完整 {count(completed)} · 部分 {count(partial)}{" "}
            · 失败 {count(failed)} · 待处理{" "}
            {count(processed === null || total === null ? null : Math.max(0, total - processed))}
          </p>
          {progressPercent === null ? null : (
            <progress aria-label="调研卡片处理进度" max={100} value={progressPercent}>
              {progressPercent}%
            </progress>
          )}
        </div>
      </div>
      <section className="collector-task-research-cards" aria-label="本次运行关联的信息卡">
        <header>
          <h3>本次运行关联的信息卡</h3>
          <span>
            {items.length} / {itemTotal}
          </span>
        </header>
        {items.length ? (
          <ul>
            {items.map((item, index) => {
              const cardId = cardIdOf(item);
              const previewCardId = previewableCardId(item);
              return (
                <li key={`${item.raw_item_id}-${cardId ?? "missing"}`}>
                  <div>
                    <strong>{(item.card?.title ?? item.title) || "未命名信息卡"}</strong>
                    <small>
                      {previewCardId
                        ? `卡片 ID：${previewCardId}`
                        : cardId
                          ? "信息卡仍在处理，暂不可预览"
                          : "尚无可预览的信息卡"}
                    </small>
                    {associationLabel(item) ? (
                      <span className="collector-task-provenance">{associationLabel(item)}</span>
                    ) : null}
                  </div>
                  <Button
                    variant="secondary"
                    size="sm"
                    type="button"
                    disabled={!previewCardId}
                    onClick={() => onPreviewCard(item)}
                  >
                    预览信息卡
                  </Button>
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="collector-task-state">当前运行尚未关联信息卡。</p>
        )}
        {trace.item_page.next_cursor ? (
          <Button variant="secondary" size="sm" type="button" disabled={loadingMore} onClick={onLoadMore}>
            加载更多信息卡（{items.length} / {itemTotal}）
          </Button>
        ) : null}
      </section>
    </section>
  );
}
