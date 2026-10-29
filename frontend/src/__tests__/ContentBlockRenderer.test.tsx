import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import ContentBlockRenderer from "../ContentBlockRenderer";
import type { ContentBlock } from "../lib/api";

function block(blockKind: string, content = "Rendered body"): ContentBlock {
  return {
    block_id: `block-${blockKind}`,
    card_id: "card-1",
    layer: "context",
    block_kind: blockKind,
    content,
    source_url: null,
    status: "enriched",
    sort_order: 0,
    provenance: null,
  };
}

describe("ContentBlockRenderer kind contract", () => {
  it.each([
    ["reddit_post", "Reddit 帖子"],
    ["reddit_metrics", "互动数据"],
    ["reddit_outbound_link", "外部链接记录"],
    ["platform_comments", "评论"],
    ["translation", "中文翻译"],
  ])("renders the %s contract with a readable label", (kind, label) => {
    render(<ContentBlockRenderer block={block(kind)} />);

    expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.getByText("Rendered body")).toBeInTheDocument();
  });

  it("keeps an unknown kind visible and safely renders it as text", () => {
    const { container } = render(
      <ContentBlockRenderer block={block("future_rich_embed", "Untrusted <script>alert(1)</script>")} />,
    );

    expect(screen.getByText("未知内容：future rich embed")).toBeInTheDocument();
    expect(screen.getByText("Untrusted <script>alert(1)</script>")).toBeInTheDocument();
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector('[data-block-kind="future_rich_embed"]')).not.toBeNull();
  });
});
