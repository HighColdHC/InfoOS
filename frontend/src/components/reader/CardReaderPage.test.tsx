import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "../../App";
import { fetchCardPreviewDetail } from "../../lib/api/cardPreview";
import { HttpRequestError } from "../../lib/api/http";
import type { CardPreviewDetail } from "../../lib/types/cardPreview";

vi.mock("../../lib/api/cardPreview", () => ({ fetchCardPreviewDetail: vi.fn() }));
const loadCollectorStatus = vi.fn();
vi.mock("../../lib/api/collectorStatus", () => ({ loadCollectorStatus: () => loadCollectorStatus() }));

const detail = {
  card: {
    card_id: "card-one", title: "单卡标题", author: "作者甲", platform: "rss",
    published_at: "2026-09-28T10:00:00Z", source_url: "https://example.test/story",
    source_label: "测试源", completeness_status: "partial", status: "active",
    captured_at: null, first_seen_at: null, updated_at: null, version: 1,
    content_hash: null, processor_version: null, raw_item_id: null, source_run_id: null,
    attachment_count: 2, engagement_metrics: {},
  },
  evaluation: null,
  opportunity: null, knowledge_cards: [],
  media_gap: null,
  body_blocks: [
    { block_id: "verification", card_id: "card-one", layer: "intelligence", block_kind: "opportunity_verification", content: "人工核实 · 记录日期 2026-10-14", source_url: null, status: "enriched", sort_order: 11, provenance: "user_verified.via_agent" },
    { block_id: "opportunity", card_id: "card-one", layer: "intelligence", block_kind: "opportunity_breakdown", content: "课程成交证据仍待核实", source_url: null, status: "enriched", sort_order: 10, provenance: "intelligence.opportunity_breakdown" },
    { block_id: "eval-summary", card_id: "card-one", layer: "intelligence", block_kind: "intel_summary", content: "评估摘要", source_url: null, status: "enriched", sort_order: 8, provenance: "intelligence.evaluate" },
    { block_id: "eval-relation", card_id: "card-one", layer: "intelligence", block_kind: "relation_note", content: "与你的关系内容", source_url: null, status: "enriched", sort_order: 9, provenance: "intelligence.evaluate" },
    { block_id: "body", card_id: "card-one", layer: "original", block_kind: "card_content", content: "### 第一段正文\n\n- 列表内容\n\n```js\nconst wide = 'a very long line';\n```\n\n[原文](https://example.test/linked) [危险链接](javascript:alert(1)) <script>alert(1)</script>", source_url: null, status: "preserved", sort_order: 1, provenance: "card content fallback" },
    { block_id: "translation", card_id: "card-one", layer: "intelligence", block_kind: "translation", content: "中文翻译内容", source_url: null, status: "preserved", sort_order: 2, provenance: null },
    { block_id: "llm-summary", card_id: "card-one", layer: "intelligence", block_kind: "summary", content: "原有 AI 解读", source_url: null, status: "enriched", sort_order: 3, provenance: null },
    { block_id: "change", card_id: "card-one", layer: "derived", block_kind: "page_change", content: "页面差异内容", source_url: null, status: "preserved", sort_order: 3, provenance: "static public HTML readable-text diff" },
    { block_id: "signal", card_id: "card-one", layer: "derived", block_kind: "signal_matches", content: "信号记录内容", source_url: null, status: "preserved", sort_order: 4, provenance: "source signal query term matches" },
    { block_id: "unknown", card_id: "card-one", layer: "derived", block_kind: "future_kind", content: "其他处理内容", source_url: null, status: "preserved", sort_order: 5, provenance: null },
  ],
  comment_blocks: [],
  image_assets: [{ asset_id: "image-one", card_id: "card-one", asset_kind: "image", asset_url: "/api/card-preview/assets/image-one" }],
  video_assets: [{ asset_id: "video-one", card_id: "card-one", asset_kind: "video", asset_url: "/api/card-preview/assets/video-one" }],
  missing_reasons: ["video_transcript_missing"],
  trace: {},
} as CardPreviewDetail;

describe("standalone card reader", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/r/card/card-one");
    vi.mocked(fetchCardPreviewDetail).mockReset();
    loadCollectorStatus.mockReset();
  });

  it("renders ordered content and same-origin attachments without management navigation", async () => {
    vi.mocked(fetchCardPreviewDetail).mockResolvedValue(detail);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "单卡标题" })).toBeInTheDocument();
    // The remote full suite can spend more than one second loading the Markdown chunk.
    expect(await screen.findByRole("heading", { name: "第一段正文", level: 3 }, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.getByText("列表内容").closest("li")).toBeInTheDocument();
    expect(screen.getByText(/const wide/).closest("pre")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "原文" })).toHaveAttribute("target", "_blank");
    expect(screen.getByRole("link", { name: "原文" })).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByRole("link", { name: "漫步" })).toHaveAttribute("href", "/r/browse");
    expect(screen.queryByRole("link", { name: "危险链接" })).not.toBeInTheDocument();
    expect(document.querySelector("script")).toBeNull();
    expect(screen.queryByText(/Unknown content|fallback|static public HTML readable-text diff|source signal query term matches/)).not.toBeInTheDocument();
    const details = screen.getByText("处理细节").closest("details");
    expect(details).not.toHaveAttribute("open");
    expect(details).toHaveTextContent("页面差异内容");
    expect(details).toHaveTextContent("信号记录内容");
    expect(details).toHaveTextContent("其他处理内容");
    expect(screen.getByRole("heading", { name: "中文翻译" })).toBeInTheDocument();
    const labels = [...document.querySelectorAll(".card-reader__blocks h2")].map((item) => item.textContent);
    expect(labels.slice(0, 2)).toEqual(["摘要", "与你的关系"]);
    expect(screen.getByRole("heading", { name: "摘要" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "历史摘要" })).toBeInTheDocument();
    expect(screen.getByText("原有 AI 解读")).toBeInTheDocument();
    expect(details).not.toHaveTextContent("与你的关系内容");
    expect(screen.getByText("中文翻译内容")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "卡片图片附件" })).toHaveAttribute("src", "/api/card-preview/assets/image-one");
    expect(screen.getByLabelText("视频附件")).toHaveAttribute("src", "/api/card-preview/assets/video-one");
    expect(screen.getByText(/部分内容尚未就绪/)).toBeInTheDocument();
    expect(screen.queryByText(/video_transcript_missing/)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "在管理面打开" })).toHaveAttribute("href", "/?menu=cards&card_id=card-one");
    expect(screen.queryByRole("button", { name: "采集任务" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "商机拆解" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "核实记录" })).toBeInTheDocument();
    expect(await screen.findByText("人工核实 · 记录日期 2026-10-14", {}, { timeout: 5000 })).toBeVisible();
    expect(await screen.findByText("课程成交证据仍待核实", {}, { timeout: 5000 })).toBeVisible();
    expect(loadCollectorStatus).not.toHaveBeenCalled();
    expect(fetchCardPreviewDetail).toHaveBeenCalledWith("card-one", expect.any(AbortSignal));
  }, 15000);

  it("shows a clear missing-card state for 404", async () => {
    vi.mocked(fetchCardPreviewDetail).mockRejectedValue(new HttpRequestError("http", "not found", 404));
    render(<App />);
    expect(await screen.findByRole("heading", { name: "找不到这张信息卡" })).toBeInTheDocument();
  });

  it.each([false, true])("keeps the attachment section for a gap without media: %s", async (hasGap) => {
    vi.mocked(fetchCardPreviewDetail).mockResolvedValue({ ...detail, image_assets: [], video_assets: [],
      media_gap: hasGap ? { reason: "youtube_missing_video", status: "blocked" } : null });
    render(<App />);
    await screen.findByRole("heading", { name: "单卡标题" });
    if (hasGap) {
      expect(screen.getByRole("region", { name: "附件" })).toHaveTextContent("媒体未下载 · YouTube 视频下载失败");
    } else {
      expect(screen.queryByRole("region", { name: "附件" })).not.toBeInTheDocument();
      expect(screen.queryByRole("note")).not.toBeInTheDocument();
    }
  });

  it("shows loading and request-error states", async () => {
    vi.mocked(fetchCardPreviewDetail).mockRejectedValue(new HttpRequestError("network", "offline"));
    render(<App />);
    expect(screen.getByRole("heading", { name: "正在加载信息卡" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "信息卡暂时无法加载" })).toBeInTheDocument();
  });
});

it.each([true, false, null, undefined])("reader renders digest badge only for true: %s", async (is_digest) => {
  window.history.replaceState({}, "", "/r/card/card-one");
  vi.mocked(fetchCardPreviewDetail).mockResolvedValue({ ...detail, card: { ...detail.card, is_digest } });
  render(<App />);
  await screen.findByRole("heading", { name: "单卡标题" });
  expect(Boolean(screen.queryByText("精华"))).toBe(is_digest === true);
});

it("信息卡头部提供商机看板深链接", async () => {
  window.history.replaceState({}, "", "/r/card/card-one");
  vi.mocked(fetchCardPreviewDetail).mockResolvedValue({ ...detail, opportunity: { has_breakdown: true, next_step: "observe" } });
  render(<App />);
  expect(await screen.findByRole("link", { name: "在商机看板打开" })).toHaveAttribute("href", "/r/opportunities?card=card-one");
  expect(screen.getByText(/商机拆解：持续观察/)).toBeInTheDocument();
});
