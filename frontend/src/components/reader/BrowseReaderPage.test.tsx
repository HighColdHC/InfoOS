import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "../../App";
import { fetchCardPreviewDetail, fetchCardPreviewFacets, fetchCardPreviewPage } from "../../lib/api/cardPreview";
import type { CardPreviewDetail, CardPreviewFacets, CardPreviewListItem } from "../../lib/types/cardPreview";

vi.mock("../../lib/api/cardPreview", () => ({ fetchCardPreviewDetail: vi.fn(), fetchCardPreviewFacets: vi.fn(), fetchCardPreviewPage: vi.fn() }));
const loadCollectorStatus = vi.fn();
vi.mock("../../lib/api/collectorStatus", () => ({ loadCollectorStatus: () => loadCollectorStatus() }));

const facets: CardPreviewFacets = {
  platforms: [{ platform: "x", label: "X", card_count: 25 }, { platform: "rss", label: "RSS", card_count: 2 }],
  sources: [{ source_id: "x-one", name: "甲的观察", platform: "x", card_count: 25 },
    { source_id: "rss-one", name: "订阅来源", platform: "rss", card_count: 2 }],
  limits: { platforms: 100, sources: 1000 }, truncated: { platforms: false, sources: false },
};
function card(index: number): CardPreviewListItem {
  return { card_id: `card ${index}`, title: `标题 ${index}`, author: "作者", platform: "x",
    first_seen_at: null, published_at: "2026-10-14T00:00:00Z", updated_at: null, version: 1, completeness_status: "complete",
    attachment_kinds: [], source_label: "甲的观察", source_url: null,
    evaluation: { quality: 80, relevance: 90, passed_quality: true, attribution_kind: "project",
      attribution_ref: null, content_type: "tool", suggested_action: "read", summary: "评估摘要",
      relation_note: "支持项目", evaluated_at: "2026-10-14T00:00:00Z",
      prompt_version: "rubric_v1", profile_version: 1, model: "external-agent" } };
}

describe("browse reader", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    window.history.replaceState({}, "", "/r/browse");
    vi.mocked(fetchCardPreviewFacets).mockResolvedValue(facets);
    vi.mocked(fetchCardPreviewPage).mockImplementation(async filters => {
      const offset = filters?.offset ?? 0;
      return { items: Array.from({ length: offset ? 1 : 20 }, (_, index) => card(offset + index)), total: 21, processing_total: 0, limit: 20, offset };
    });
  });

  it("routes without management, renders scores and links, and loads 20 more by offset", async () => {
    render(<App />);
    expect(await screen.findByRole("link", { name: "标题 0" })).toHaveAttribute("href", "/r/card/card%200");
    expect(screen.getAllByText("质量 80")).toHaveLength(20);
    expect(screen.getAllByText("相关 90")).toHaveLength(20);
    expect(screen.getAllByText("评估摘要")).toHaveLength(20);
    expect(screen.getAllByText("与你的关系：支持项目")).toHaveLength(20);
    expect(screen.getAllByText("归属：项目")).toHaveLength(20);
    expect(fetchCardPreviewPage).toHaveBeenLastCalledWith(expect.objectContaining({ evaluation: "passed", sort: "published_desc", limit: 20, offset: 0 }), expect.any(AbortSignal));
    fireEvent.click(screen.getByRole("button", { name: "加载更多" }));
    expect(await screen.findByRole("link", { name: "标题 20" })).toBeInTheDocument();
    expect(screen.getByText("已显示 21 / 21 条 · 已到底")).toBeInTheDocument();
    expect(fetchCardPreviewPage).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 20, limit: 20 }), expect.any(AbortSignal));
    expect(screen.queryByRole("button", { name: "加载更多" })).not.toBeInTheDocument();
    expect(loadCollectorStatus).not.toHaveBeenCalled();
  });

  it("writes filters to URL, switches source choices and removes invalid relevance sorting", async () => {
    render(<App />);
    await screen.findByRole("link", { name: "标题 0" });
    expect(screen.getByLabelText("来源")).toBeDisabled();
    fireEvent.change(screen.getByLabelText("平台"), { target: { value: "x" } });
    expect(screen.getByRole("option", { name: "甲的观察（25）" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "订阅来源（2）" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("来源"), { target: { value: "x-one" } });
    fireEvent.change(screen.getByLabelText("排序"), { target: { value: "relevance_desc" } });
    fireEvent.change(screen.getByLabelText("关键词"), { target: { value: "  AI 产品  " } });
    fireEvent.click(screen.getByRole("button", { name: "搜索" }));
    const params = new URLSearchParams(window.location.search);
    expect(Object.fromEntries(params)).toEqual({ platform: "x", source_id: "x-one", evaluation: "passed", sort: "relevance_desc", query: "AI 产品" });
    fireEvent.change(screen.getByLabelText("平台"), { target: { value: "rss" } });
    expect(screen.getByRole("option", { name: "订阅来源（2）" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "甲的观察（25）" })).not.toBeInTheDocument();
    expect(new URLSearchParams(window.location.search).has("source_id")).toBe(false);
    fireEvent.change(screen.getByLabelText("视图"), { target: { value: "evaluated" } });
    expect(screen.queryByRole("option", { name: "相关度" })).not.toBeInTheDocument();
    await waitFor(() => expect(fetchCardPreviewPage).toHaveBeenLastCalledWith(expect.objectContaining({ platform: "rss", source_id: "", evaluation: "evaluated", sort: "published_desc", query: "AI 产品", offset: 0 }), expect.any(AbortSignal)));
  });

  it("keeps modified title clicks native and opens an ordinary click by card id", async () => {
    const detail: CardPreviewDetail = {
      card: { ...card(0), captured_at: null, status: "active", content_hash: null, processor_version: null,
        raw_item_id: null, source_run_id: null, attachment_count: 0, engagement_metrics: {} },
      knowledge_cards: [], opportunity: null, body_blocks: [], comment_blocks: [], image_assets: [], video_assets: [],
      missing_reasons: [], media_gap: null, trace: {}, evaluation: null,
    };
    vi.mocked(fetchCardPreviewDetail).mockResolvedValue(detail);
    render(<App />);
    const title = await screen.findByRole("link", { name: "标题 0" });
    expect(fireEvent.click(title, { metaKey: true })).toBe(true);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(fetchCardPreviewDetail).not.toHaveBeenCalled();
    expect(fireEvent.click(title)).toBe(false);
    const dialog = await screen.findByRole("dialog", { name: "标题 0" });
    expect(within(dialog).getByRole("heading", { name: "标题 0" })).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "新标签页打开" })).toHaveAttribute("href", "/r/card/card%200");
    expect(fetchCardPreviewDetail).toHaveBeenCalledWith("card 0", expect.any(AbortSignal));
    expect(window.location.pathname).toBe("/r/browse");
  });

  it("restores shared filters and history, renders low scores, opportunity and unevaluated cards", async () => {
    window.history.replaceState({}, "", "/r/browse?platform=x&source_id=x-one&evaluation=evaluated&query=证据");
    const low = card(1);
    low.evaluation = { ...low.evaluation!, quality: 40, passed_quality: false, relevance: null };
    low.opportunity = { has_breakdown: true, next_step: "verify" };
    vi.mocked(fetchCardPreviewPage).mockResolvedValue({ items: [low, { ...card(2), evaluation: null }], total: 2, processing_total: 0, limit: 20, offset: 0 });
    render(<App />);
    const link = await screen.findByRole("link", { name: "标题 1" });
    const article = link.closest("article")!;
    expect(article).toHaveClass("browse-reader__card--below");
    expect(within(article).getByText("未过线")).toBeInTheDocument();
    expect(within(article).getByText("商机")).toBeInTheDocument();
    expect(within(article).getByText(/AI 建议：待核实/)).toBeInTheDocument();
    expect(screen.getByText("尚未评估")).toBeInTheDocument();
    expect(screen.getByLabelText("关键词")).toHaveValue("证据");
    expect(fetchCardPreviewPage).toHaveBeenLastCalledWith(expect.objectContaining({ platform: "x", source_id: "x-one", evaluation: "evaluated", query: "证据" }), expect.any(AbortSignal));
    act(() => {
      window.history.replaceState({}, "", "/r/browse?evaluation=unevaluated");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(screen.getByLabelText("关键词")).toHaveValue("");
    await waitFor(() => expect(fetchCardPreviewPage).toHaveBeenLastCalledWith(expect.objectContaining({ evaluation: "unevaluated", offset: 0 }), expect.any(AbortSignal)));
  });

  it("retries a failed next page without losing earlier cards", async () => {
    render(<App />);
    await screen.findByRole("link", { name: "标题 0" });
    vi.mocked(fetchCardPreviewPage).mockRejectedValueOnce(new Error("offline"));
    fireEvent.click(screen.getByRole("button", { name: "加载更多" }));
    await screen.findByRole("alert");
    expect(screen.getByRole("link", { name: "标题 0" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await screen.findByRole("link", { name: "标题 20" });
    expect(screen.getByText("已显示 21 / 21 条 · 已到底")).toBeInTheDocument();
  });

  it("shows an empty state and ignores stale requests after a filter change", async () => {
    let resolveOld!: (value: Awaited<ReturnType<typeof fetchCardPreviewPage>>) => void;
    vi.mocked(fetchCardPreviewPage).mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }));
    render(<App />);
    expect(screen.getByRole("status")).toHaveTextContent("正在加载");
    vi.mocked(fetchCardPreviewPage).mockResolvedValue({ items: [], total: 0, processing_total: 0, limit: 20, offset: 0 });
    fireEvent.change(screen.getByLabelText("视图"), { target: { value: "unevaluated" } });
    await screen.findByText("没有符合筛选条件的信息卡。");
    await act(async () => resolveOld({ items: [card(99)], total: 1, processing_total: 0, limit: 20, offset: 0 }));
    expect(screen.queryByRole("link", { name: "标题 99" })).not.toBeInTheDocument();
  });
});
