import { lazy, Suspense } from "react";
import type { ContentBlock } from "../../lib/types/card";
import { contentBlockPresentation } from "../../lib/contentBlockLabels";
import { safeExternalUrl } from "../../lib/cardPresentation";

const ReaderMarkdown = lazy(() => import("./ReaderMarkdown"));

export function isReaderDetail(block: ContentBlock): boolean {
  return contentBlockPresentation(block.block_kind).category === "detail";
}

export function ReaderBlock({ block }: { block: ContentBlock }) {
  const presentation = contentBlockPresentation(block.block_kind);
  const label = presentation.primary ? null : presentation.label;
  const sourceUrl = safeExternalUrl(block.source_url);
  return (
    <section className="card-reader__block" data-kind={block.block_kind}>
      {label ? <h2 className="card-reader__block-label">{label}</h2> : null}
      <div className="card-reader__markdown">
        <Suspense fallback={<p role="status">正在加载正文…</p>}>
          <ReaderMarkdown>{block.content}</ReaderMarkdown>
        </Suspense>
      </div>
      {sourceUrl ? <a className="card-reader__block-source" href={sourceUrl} target="_blank" rel="noopener noreferrer">查看内容来源 ↗</a> : null}
    </section>
  );
}
