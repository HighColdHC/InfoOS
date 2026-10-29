import { Github, Globe, Rss, Youtube } from "lucide-react";
import type { ReactNode } from "react";
import { platformLabel } from "../../lib/platforms";

const GLYPHS: Record<string, ReactNode> = {
  x: <span className="platform-mark__text">𝕏</span>,
  youtube: <Youtube size={13} strokeWidth={2.2} />,
  rss: <Rss size={12} strokeWidth={2.6} />,
  reddit: <span className="platform-mark__text">r/</span>,
  github: <Github size={13} strokeWidth={2.2} />,
  hackernews: <span className="platform-mark__text">Y</span>,
  producthunt: <span className="platform-mark__text">P</span>,
  xiaohongshu: <span className="platform-mark__text">红</span>,
  douyin: <span className="platform-mark__text">抖</span>,
  zsxq: <span className="platform-mark__text">生</span>,
  scys: <span className="platform-mark__text">生</span>,
};

/** Small branded chip that identifies a platform at a glance. */
export function PlatformMark({ platform, size = "md", withLabel = false }: {
  platform: string | null | undefined;
  size?: "sm" | "md" | "lg";
  withLabel?: boolean;
}) {
  const key = platform ?? "";
  const glyph = GLYPHS[key] ?? <Globe size={13} strokeWidth={2.2} />;
  return (
    <span className={`platform-mark platform-mark--${size}`} data-platform={key || "web"}>
      <span className="platform-mark__glyph" aria-hidden="true">{glyph}</span>
      {withLabel ? <span className="platform-mark__label">{platformLabel(platform)}</span> : null}
    </span>
  );
}
