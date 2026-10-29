import { useEffect } from "react";
import { ChevronLeft, ChevronRight, Info } from "lucide-react";
import { Modal } from "../../../ui/Modal";
import type { CardPreviewAsset } from "../../../../lib/types/cardPreview";

type Props = {
  assets: CardPreviewAsset[];
  index: number;
  onIndexChange: (index: number) => void;
  onClose: () => void;
};

export function MediaPreviewModal({ assets, index, onIndexChange, onClose }: Props) {
  const asset = index >= 0 ? assets.at(index) : undefined;
  useEffect(() => {
    if (!asset || assets.length < 2) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "ArrowLeft" && index > 0) onIndexChange(index - 1);
      if (event.key === "ArrowRight" && index < assets.length - 1) onIndexChange(index + 1);
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [asset, assets.length, index, onIndexChange]);
  if (!asset) return null;
  const isVideo = asset.asset_kind === "video";
  const title = isVideo ? "视频预览" : "图片预览";
  const hasMultiple = assets.length > 1;
  return (
    <Modal
      open
      title={
        <span className="collector-media-preview__title">
          {title}
          {hasMultiple ? ` · ${index + 1}/${assets.length}` : ""}
        </span>
      }
      ariaLabel={title}
      onClose={onClose}
      size="fullscreen"
      headerActions={
        <details key={asset.asset_id} className="collector-media-preview__details">
          <summary aria-label="查看附件信息" title="查看附件信息" tabIndex={0}>
            <Info size={18} aria-hidden="true" />
          </summary>
          <dl className="collector-media-preview__facts">
            <div><dt>附件 ID</dt><dd>{asset.asset_id}</dd></div>
            <div><dt>所属卡片</dt><dd>{asset.card_id}</dd></div>
            <div><dt>处理状态</dt><dd>{asset.status || "未知"}</dd></div>
            <div>
              <dt>文件事实</dt>
              <dd>
                {[fileName(asset.local_path), asset.mime_type, formatBytes(asset.size_bytes)]
                  .filter(Boolean)
                  .join(" · ") || "暂无"}
              </dd>
            </div>
          </dl>
        </details>
      }
    >
      <div className="collector-media-preview">
        <div className="collector-media-preview__stage">
          {isVideo ? (
            <video
              key={asset.asset_id}
              controls
              autoPlay
              playsInline
              preload="metadata"
              src={asset.asset_url}
              aria-label={`视频 ${asset.asset_id}`}
            />
          ) : (
            <img src={asset.asset_url} alt={`图片 ${asset.asset_id}`} />
          )}
          {hasMultiple ? (
            <>
              <button
                type="button"
                className="collector-media-preview__control collector-media-preview__control--previous"
                onClick={() => onIndexChange(index - 1)}
                disabled={index === 0}
                aria-label="上一项媒体"
              >
                <ChevronLeft size={24} />
              </button>
              <button
                type="button"
                className="collector-media-preview__control collector-media-preview__control--next"
                onClick={() => onIndexChange(index + 1)}
                disabled={index === assets.length - 1}
                aria-label="下一项媒体"
              >
                <ChevronRight size={24} />
              </button>
              <span className="collector-media-preview__counter">
                {index + 1} / {assets.length}
              </span>
            </>
          ) : null}
        </div>
      </div>
    </Modal>
  );
}

function fileName(path?: string | null): string | null {
  if (!path) return null;
  return path.split(/[\\/]/).pop() || null;
}

function formatBytes(value?: number | null): string | null {
  if (value == null) return null;
  if (value < 1024) return `${value} B`;
  return `${(value / 1024).toFixed(value < 10240 ? 1 : 0)} KB`;
}
