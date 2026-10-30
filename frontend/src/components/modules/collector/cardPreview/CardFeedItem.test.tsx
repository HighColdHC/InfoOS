import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CardFeedItem } from "./CardFeedItem";
import type { CardPreviewListItem } from "../../../../lib/types/cardPreview";

const item: CardPreviewListItem = {
  card_id: "card", title: "星球帖子", author: "作者", platform: "zsxq", first_seen_at: null, published_at: null,
  completeness_status: "complete", attachment_kinds: [], version: 1, updated_at: null,
  source_label: "星球", source_url: null, evaluation: null,
};

describe("digest badge", () => {
  it.each([true, false, null, undefined])("renders only explicit true: %s", (is_digest) => {
    render(<CardFeedItem item={{ ...item, is_digest }} selected={false} onSelect={() => {}} />);
    expect(Boolean(screen.queryByText("精华"))).toBe(is_digest === true);
  });
});

describe("evaluated feed excerpt", () => {
  it.each(["passed", "below", "none"])("keeps the excerpt only when not passed: %s", (state) => {
    const evaluation = state === "none" ? null : {
      passed_quality: state === "passed", quality: 90, relevance: null,
      evaluated_at: "2026-10-20T00:00:00Z", prompt_version: "rubric_v1", profile_version: 1, model: "test",
      summary: "评估摘要", relation_note: "关系说明", attribution_kind: "project",
      attribution_ref: null, content_type: "method", suggested_action: "read",
    };
    render(<CardFeedItem item={{ ...item, excerpt: "原文摘录", evaluation }} selected={false} onSelect={() => {}} />);
    expect(Boolean(screen.queryByText("原文摘录"))).toBe(state !== "passed");
    if (state === "passed") {
      expect(screen.getByText("评估摘要")).toBeInTheDocument();
      expect(screen.getByText("与你的关系：关系说明")).toBeInTheDocument();
      expect(screen.getByText(/质量 90 · 相关 —/)).toBeInTheDocument();
    }
  });
});

describe("video cover thumbnails", () => {
  it.each([true, false])("uses a thumbnail when available: %s", (hasThumbnail) => {
    const { container } = render(<CardFeedItem item={{ ...item, cover_kind: "video", cover_asset_url: "/original.mp4",
      cover_thumbnail_url: hasThumbnail ? "/thumbnail.webp" : null }} selected={false} onSelect={() => {}} />);
    const cover = container.querySelector(".card-feed-item__cover")!;
    expect(cover.querySelector("img")?.getAttribute("src") ?? null).toBe(hasThumbnail ? "/thumbnail.webp" : null);
    expect(Boolean(cover.querySelector("video"))).toBe(!hasThumbnail);
    if (!hasThumbnail) expect(cover.querySelector("video")).toHaveAttribute("src", "/original.mp4");
    expect(cover.querySelector('[aria-label="视频"]')).toBeInTheDocument();
  });
});
