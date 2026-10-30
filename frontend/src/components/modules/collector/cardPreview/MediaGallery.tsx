import { Play } from "lucide-react";
import type { CardPreviewAsset } from "../../../../lib/types/cardPreview";

type Props = {
  assets: CardPreviewAsset[];
  variant?: "feed" | "detail";
  onPreview: (assets: CardPreviewAsset[], index: number) => void;
};

export function MediaGallery({ assets, variant = "detail", onPreview }: Props) {
  if (!assets.length) return null;
  const density = assets.length === 1 ? "single" : assets.length === 2 ? "pair" : "grid";
  const visibleAssets = variant === "feed" ? assets.slice(0, 4) : assets;
  return (
    <div
      className="collector-card-media-gallery"
      data-density={density}
      data-variant={variant}
      data-count={Math.min(assets.length, 4)}
      aria-label="媒体缩略图宫格"
    >
      {visibleAssets.map((asset, index) => {
        const isVideo = asset.asset_kind === "video";
        const hiddenCount = index === visibleAssets.length - 1 ? assets.length - visibleAssets.length : 0;
        return (
          <button
            type="button"
            className="collector-card-media-gallery__item"
            key={asset.asset_id}
            onClick={() => onPreview(assets, index)}
            aria-label={`预览${isVideo ? "视频" : "图片"} ${asset.asset_id}`}
          >
            {asset.thumbnail_url ? (
              <img src={asset.thumbnail_url} alt="" loading="lazy" decoding="async" />
            ) : isVideo ? (
              <video src={asset.asset_url} muted playsInline preload="metadata" aria-hidden="true" />
            ) : (
              <img src={asset.asset_url} alt="" loading="lazy" />
            )}
            {isVideo ? <span className="collector-card-media-gallery__play" aria-hidden="true"><Play size={20} fill="currentColor" /></span> : null}
            <span className="collector-card-media-gallery__kind">{isVideo ? "视频" : "图片"}</span>
            {hiddenCount > 0 ? <span className="collector-card-media-gallery__more">+{hiddenCount}</span> : null}
            {assets.length > 1 && hiddenCount === 0 ? <span className="collector-card-media-gallery__count">{index + 1} / {assets.length}</span> : null}
          </button>
        );
      })}
    </div>
  );
}
