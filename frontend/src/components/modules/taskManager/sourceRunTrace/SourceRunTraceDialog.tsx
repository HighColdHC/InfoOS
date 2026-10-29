import { Button } from "../../../ui/primitives/Button";
import { isAbort } from "../../../../lib/isAbort";
import { Modal } from "../../../ui/Modal";
import { useEffect, useRef, useState } from "react";
import { fetchCardPreviewDetail } from "../../../../lib/api/cardPreview";
import type { CardPreviewAsset, CardPreviewDetail } from "../../../../lib/types/cardPreview";
import type { SourceRunListItem, SourceRunTrace } from "../../../../lib/types/sourceRuns";
import { MediaPreviewModal } from "../../collector/cardPreview/MediaPreviewModal";
import { ResearchRunDetailView } from "./ResearchRunDetailView";
import { SourceRunTraceView } from "./SourceRunTraceView";
import { CardReaderModal, type CardReaderTarget } from "../../../reader/CardReaderModal";

type Props = {
  selected: SourceRunListItem | null;
  trace: SourceRunTrace | null;
  loading: boolean;
  loadingMore: boolean;
  error: string | null;
  retryingTaskId: string | null;
  onClose: () => void;
  onReload: () => void;
  onLoadMore: () => void;
  onCopy: (value: string, label: string) => void;
  onRetry: (taskId: string) => Promise<void>;
};

export function SourceRunTraceDialog(props: Props) {
  const [readerTarget, setReaderTarget] = useState<CardReaderTarget | null>(null);
  const [cardDetail, setCardDetail] = useState<CardPreviewDetail | null>(null);
  const [cardLoading, setCardLoading] = useState(false);
  const [cardError, setCardError] = useState<string | null>(null);
  const [mediaPreview, setMediaPreview] = useState<{ assets: CardPreviewAsset[]; index: number } | null>(null);
  const cardRequest = useRef<{ controller: AbortController; runId: string; cardId: string } | null>(null);
  const selectedRunId = props.selected?.source_run_id ?? null;
  const selectedRunIdRef = useRef(selectedRunId);
  selectedRunIdRef.current = selectedRunId;
  useEffect(() => {
    cardRequest.current?.controller.abort();
    cardRequest.current = null;
    setCardDetail(null);
    setCardLoading(false);
    setCardError(null);
    setMediaPreview(null);
    setReaderTarget(null);
    return () => {
      cardRequest.current?.controller.abort();
      cardRequest.current = null;
    };
  }, [selectedRunId]);
  const isResearch = props.selected?.task_view === "research";
  const title = props.selected
    ? `${isResearch ? "调研详情" : "来源运行追踪"} · ${props.selected.source_name}`
    : "来源运行追踪";
  const previewCard = (cardId: string | null | undefined) => {
    const runId = selectedRunIdRef.current;
    if (!cardId || !runId || cardLoading) return;
    cardRequest.current?.controller.abort();
    const controller = new AbortController();
    cardRequest.current = { controller, runId, cardId };
    setCardLoading(true);
    setCardError(null);
    setCardDetail(null);
    void fetchCardPreviewDetail(cardId, controller.signal)
      .then((detail) => {
        const request = cardRequest.current;
        if (request?.controller === controller && selectedRunIdRef.current === runId && request.cardId === cardId)
          setCardDetail(detail);
      })
      .catch((reason: unknown) => {
        if (!isAbort(reason) && cardRequest.current?.controller === controller && selectedRunIdRef.current === runId) {
          setCardError(reason instanceof Error ? reason.message : String(reason));
        }
      })
      .finally(() => {
        if (cardRequest.current?.controller === controller) {
          cardRequest.current = null;
          setCardLoading(false);
        }
      });
  };
  const cancelCardRequest = () => {
    cardRequest.current?.controller.abort();
    cardRequest.current = null;
    setCardLoading(false);
  };
  const backToResearch = () => {
    cancelCardRequest();
    setCardDetail(null);
    setCardError(null);
    setMediaPreview(null);
  };
  const closeDialog = () => {
    cancelCardRequest();
    setMediaPreview(null);
    setReaderTarget(null);
    props.onClose();
  };
  return (
    <>
      <Modal
        open={Boolean(props.selected) && !mediaPreview}
        onClose={closeDialog}
        title={title}
        ariaLabel={isResearch ? "调研详情" : "来源运行追踪"}
        width={1120}
      >
        <div className="collector-task-dialog-scroll">
          {props.loading ? (
            <div className="collector-task-state" role="status">
              正在读取持久化追踪证据…
            </div>
          ) : null}
          {props.error ? (
            <div className="collector-task-state is-error" role="alert">
              <span>追踪读取失败：{props.error}</span>
              <Button variant="secondary" size="sm" type="button" onClick={props.onReload}>
                重试读取
              </Button>
            </div>
          ) : null}
          {props.trace && isResearch ? (
            <ResearchRunDetailView
              trace={props.trace}
              detail={cardDetail}
              detailLoading={cardLoading}
              detailError={cardError}
              onPreviewCard={(item) => previewCard(item.card?.id ?? item.card_id)}
              onPreviewMedia={(assets, index) => setMediaPreview({ assets, index })}
              onRead={detail => setReaderTarget({ cardId: detail.card.card_id, detail })}
              onBack={backToResearch}
              onLoadMore={props.onLoadMore}
              loadingMore={props.loadingMore}
            />
          ) : null}
          {props.trace && !isResearch ? (
            <SourceRunTraceView
              trace={props.trace}
              blockingReason={props.selected?.blocking_reason}
              onCopy={props.onCopy}
              onRetry={props.onRetry}
              onTaskCancelled={props.onReload}
              retryingTaskId={props.retryingTaskId}
              onLoadMore={props.onLoadMore}
              loadingMore={props.loadingMore}
            />
          ) : null}
          {props.trace && isResearch && !cardDetail && !cardLoading && !cardError ? (
            <details className="collector-task-technical-trace">
              <summary>技术追踪证据</summary>
              <SourceRunTraceView
                trace={props.trace}
                blockingReason={props.selected?.blocking_reason}
                onCopy={props.onCopy}
                onRetry={props.onRetry}
                onTaskCancelled={props.onReload}
                retryingTaskId={props.retryingTaskId}
                onLoadMore={props.onLoadMore}
                loadingMore={props.loadingMore}
              />
            </details>
          ) : null}
        </div>
      </Modal>
      <CardReaderModal target={readerTarget} onClose={() => setReaderTarget(null)} />
      <MediaPreviewModal
        assets={mediaPreview?.assets ?? []}
        index={mediaPreview?.index ?? 0}
        onIndexChange={(index) => setMediaPreview((current) => (current ? { ...current, index } : null))}
        onClose={() => setMediaPreview(null)}
      />
    </>
  );
}
