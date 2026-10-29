import "../../styles/_card-reader.css";
import { useEffect, useState } from "react";
import { ReaderContent } from "./ReaderContent";
import { fetchCardPreviewDetail } from "../../lib/api/cardPreview";
import { HttpRequestError } from "../../lib/api/http";
import type { CardPreviewDetail } from "../../lib/types/cardPreview";
import { ReaderChrome } from "./ReaderChrome";

type ReaderState =
  | { kind: "loading" }
  | { kind: "ready"; detail: CardPreviewDetail }
  | { kind: "missing" }
  | { kind: "error" };

function cardIdFromPath(): string | null {
  const segment = window.location.pathname.match(/^\/r\/card\/([^/]+)\/?$/)?.[1];
  if (!segment) return null;
  try {
    return decodeURIComponent(segment);
  } catch {
    return null;
  }
}

export function CardReaderPage() {
  const cardId = cardIdFromPath();
  const [state, setState] = useState<ReaderState>({ kind: "loading" });

  useEffect(() => {
    if (!cardId) {
      setState({ kind: "missing" });
      return;
    }
    const controller = new AbortController();
    setState({ kind: "loading" });
    void fetchCardPreviewDetail(cardId, controller.signal).then(
      (detail) => { if (!controller.signal.aborted) setState({ kind: "ready", detail }); },
      (error: unknown) => {
        if (controller.signal.aborted) return;
        setState({ kind: error instanceof HttpRequestError && error.status === 404 ? "missing" : "error" });
      },
    );
    return () => controller.abort();
  }, [cardId]);

  return (
    <ReaderChrome active="card">
        {state.kind === "loading" ? <ReaderMessage title="正在加载信息卡" description="正在读取卡片内容…" /> : null}
        {state.kind === "missing" ? <ReaderMessage title="找不到这张信息卡" description="卡片不存在，或当前还没有可阅读的版本。" /> : null}
        {state.kind === "error" ? <ReaderMessage title="信息卡暂时无法加载" description="读取失败，请稍后刷新页面重试。" /> : null}
        {state.kind === "ready" ? <ReaderContent detail={state.detail} /> : null}
        {cardId ? (
          <footer className="card-reader__footer">
            <a href={`/?menu=cards&card_id=${encodeURIComponent(cardId)}`}>在管理面打开</a>
            <span>登录后可管理</span>
          </footer>
        ) : null}
    </ReaderChrome>
  );
}

function ReaderMessage({ title, description }: { title: string; description: string }) {
  return <div className="card-reader__message" role="status"><span className="card-reader__message-mark" aria-hidden="true" /><h1>{title}</h1><p>{description}</p></div>;
}
