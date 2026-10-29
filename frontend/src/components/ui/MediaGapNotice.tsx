import "../../styles/_media-gap.css";
import { ArrowUpRight, TriangleAlert } from "lucide-react";
import { safeExternalUrl } from "../../lib/cardPresentation";
import { mediaGapLabel } from "../../lib/mediaGapLabels";
import type { CardPreviewDetail } from "../../lib/types/cardPreview";

type Props = {
  gap: CardPreviewDetail["media_gap"];
  hasAttachments: boolean;
  sourceUrl: string | null;
};

export function MediaGapNotice({ gap, hasAttachments, sourceUrl }: Props) {
  if (!gap) return null;
  const url = safeExternalUrl(sourceUrl);
  return (
    <div className="media-gap-notice" role="note" aria-label="媒体下载提示">
      <TriangleAlert size={16} aria-hidden="true" />
      <p>{hasAttachments ? "部分媒体未下载" : "媒体未下载"} · {mediaGapLabel(gap.reason)}</p>
      {url ? <a href={url} target="_blank" rel="noreferrer">去原平台查看<ArrowUpRight size={14} aria-hidden="true" /></a> : null}
    </div>
  );
}
