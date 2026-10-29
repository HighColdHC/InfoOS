import "../../styles/_card-reader.css";
import { ArrowUpRight } from "lucide-react";
import { useEffect, useState } from "react";
import { fetchCardPreviewDetail } from "../../lib/api/cardPreview";
import { HttpRequestError } from "../../lib/api/http";
import type { CardPreviewDetail } from "../../lib/types/cardPreview";
import { Modal } from "../ui/Modal";
import { ReaderContent } from "./ReaderContent";

export type CardReaderTarget = { cardId: string; detail?: CardPreviewDetail };
type ReaderState =
  | { kind: "loading" }
  | { kind: "ready"; detail: CardPreviewDetail }
  | { kind: "missing" | "error" };

export function CardReaderModal({ target, onClose }: { target: CardReaderTarget | null; onClose: () => void }) {
  return target ? <CardReaderDialog key={target.cardId} target={target} onClose={onClose} /> : null;
}

function CardReaderDialog({ target, onClose }: { target: CardReaderTarget; onClose: () => void }) {
  const [state, setState] = useState<ReaderState>(() => target.detail ? { kind: "ready", detail: target.detail } : { kind: "loading" });
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (target.detail) {
      setState({ kind: "ready", detail: target.detail });
      return;
    }
    const controller = new AbortController();
    setState({ kind: "loading" });
    void fetchCardPreviewDetail(target.cardId, controller.signal).then(
      detail => { if (!controller.signal.aborted) setState({ kind: "ready", detail }); },
      (error: unknown) => {
        if (controller.signal.aborted) return;
        setState({ kind: error instanceof HttpRequestError && error.status === 404 ? "missing" : "error" });
      },
    );
    return () => controller.abort();
  }, [target.cardId, target.detail, retry]);

  return (
    <Modal
      open
      onClose={onClose}
      title="阅读"
      ariaLabel={state.kind === "ready" ? state.detail.card.title || "未命名信息卡" : "信息卡"}
      width={860}
      headerActions={(
        <a className="btn" href={`/r/card/${encodeURIComponent(target.cardId)}`} target="_blank" rel="noopener">
          新标签页打开 <ArrowUpRight size={14} aria-hidden="true" />
        </a>
      )}
    >
      <div className="standalone-card-reader card-reader-embedded"><div className="card-reader__page">
        {state.kind === "ready" ? <ReaderContent detail={state.detail} /> : <div className="card-reader__message" role="status">
          <h1>{state.kind === "loading" ? "正在加载信息卡…" : state.kind === "missing" ? "找不到这张信息卡" : "信息卡暂时无法加载"}</h1>
          {state.kind !== "loading" ? <button className="btn" type="button" onClick={() => setRetry(value => value + 1)}>重试</button> : null}
        </div>}
      </div></div>
    </Modal>
  );
}
