import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CardPreviewModule } from "../CardPreviewModule";
import * as cardPreviewApi from "../../../../lib/api/cardPreview";
import type { CardPreviewDetail, CardPreviewPage } from "../../../../lib/types/cardPreview";

vi.mock("../../../../lib/api/cardPreview", async () => {
  const actual = await vi.importActual<typeof import("../../../../lib/api/cardPreview")>(
    "../../../../lib/api/cardPreview",
  );
  return {
    ...actual,
    fetchCardPreviewFacets: vi.fn(),
    fetchCardPreviewPage: vi.fn(),
    fetchCardPreviewDetail: vi.fn(),
    fetchCapturePreview: vi.fn(),
  };
});

const page: CardPreviewPage = {
  items: [
    {
      card_id: "card-1",
      evaluation: null,
      title: "第一张信息卡",
      author: "作者甲",
      platform: "xiaohongshu",
      first_seen_at: null, published_at: "2026-07-16T08:30:00Z",
      completeness_status: "complete",
      attachment_kinds: ["image", "video"],
      version: 3,
      updated_at: "2026-07-16T09:00:00Z",
      source_label: "小红书 · 作者甲",
      source_url: "https://www.xiaohongshu.com/explore/1",
    },
    {
      card_id: "card-2",
      evaluation: null,
      title: "第二张信息卡",
      author: "作者乙",
      platform: "youtube",
      first_seen_at: null, published_at: "2026-07-15T08:30:00Z",
      completeness_status: "partial",
      attachment_kinds: [],
      version: 1,
      updated_at: "2026-07-15T09:00:00Z",
      source_label: "YouTube · 作者乙",
      source_url: "https://youtube.com/watch?v=2",
    },
  ],
  total: 2,
  processing_total: 2,
  limit: 25,
  offset: 0,
};

function detail(cardId = "card-1"): CardPreviewDetail {
  return {
    opportunity: null, knowledge_cards: [],
    media_gap: null,
    card: {
      card_id: cardId,
      title: cardId === "card-1" ? "第一张信息卡" : "第二张信息卡",
      author: cardId === "card-1" ? "作者甲" : "作者乙",
      platform: cardId === "card-1" ? "xiaohongshu" : "youtube",
      first_seen_at: null, published_at: "2026-07-16T08:30:00Z",
      captured_at: "2026-07-16T08:31:00Z",
      completeness_status: cardId === "card-1" ? "complete" : "partial",
      status: "active",
      version: 3,
      content_hash: "sha256:1234567890abcdef",
      processor_version: "collector/1.0",
      updated_at: "2026-07-16T09:00:00Z",
      source_label: "小红书 · 作者甲",
      source_url: "https://www.xiaohongshu.com/explore/1",
      raw_item_id: "raw-1",
      source_run_id: "run-1",
      attachment_count: 2,
      engagement_metrics: {
        view_count: 1200,
        like_count: 0,
        comment_count: 24,
        share_count: 8,
      },
    },
    evaluation: null,
    body_blocks: [
      {
        block_id: "body-1",
        card_id: cardId,
        layer: "original",
        block_kind: "card_content",
        content: "这是用户可读的正文。",
        source_url: null,
        status: "preserved",
        sort_order: 0,
        provenance: "source",
      },
      {
        block_id: "transcript-1",
        card_id: cardId,
        layer: "derived",
        block_kind: "video_transcript",
        content: "这是视频转录内容。",
        source_url: null,
        status: "enriched",
        sort_order: 1,
        provenance: "transcription",
      },
    ],
    comment_blocks: [
      {
        block_id: "comment-1",
        card_id: cardId,
        layer: "original",
        block_kind: "platform_comments",
        content: "这是一条评论。",
        source_url: null,
        status: "preserved",
        sort_order: 2,
        provenance: "source",
        raw_payload: {
          comments: [
            { author: "读者一", text: "这是一条评论。", likes: 3 },
            { author: "读者二", text: "这是另一条评论。", likes: 1 },
          ],
        },
      },
    ],
    image_assets: [
      {
        asset_id: "image-1",
        card_id: cardId,
        asset_kind: "image",
        mime_type: "image/webp",
        status: "downloaded",
        local_path: "/data/assets/image-1.webp",
        size_bytes: 2048,
        asset_url: "/api/card-preview/assets/image-1",
      },
    ],
    video_assets: [
      {
        asset_id: "video-1",
        card_id: cardId,
        asset_kind: "video",
        mime_type: "video/mp4",
        status: "downloaded",
        local_path: "/data/assets/video-1.mp4",
        size_bytes: 4096,
        asset_url: "/api/card-preview/assets/video-1",
      },
    ],
    missing_reasons: cardId === "card-1" ? [] : ["缺少视频转录"],
    trace: { raw_connector_id: "opencli", raw_item_id: "raw-1", source_run_id: "run-1" },
  };
}

const listMock = vi.mocked(cardPreviewApi.fetchCardPreviewPage);
const detailMock = vi.mocked(cardPreviewApi.fetchCardPreviewDetail);
const captureMock = vi.mocked(cardPreviewApi.fetchCapturePreview);

describe("CardPreviewModule", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/?menu=cards");
    vi.mocked(cardPreviewApi.fetchCardPreviewFacets).mockResolvedValue({
      platforms: [], sources: [
        { source_id: "__none__", platform: "rss", name: "未关联来源", card_count: 42, evaluated_count: 8, passed_count: 3 },
        { source_id: "__none__", platform: "x", name: "未关联来源", card_count: 9, evaluated_count: 0, passed_count: 0 },
      ], limits: { platforms: null, sources: null }, truncated: { platforms: false, sources: false },
    });
    listMock.mockImplementation(async (filters) => filters?.status === "processing"
      ? { items: [], total: 2, processing_total: 2, limit: filters.limit ?? 30, offset: 0 }
      : { ...page, limit: filters?.limit ?? 30, offset: filters?.offset ?? 0 });
    detailMock.mockImplementation(async (cardId) => detail(cardId));
    captureMock.mockResolvedValue({ capture_id: "cap-1", processing_status: "succeeded", card_id: "card-1",
      infoos_url: "/?menu=cards&capture_id=cap-1", error_code: null, updated_at: "2026-07-30T10:00:00Z" });
  });
  afterEach(() => { vi.clearAllMocks(); vi.unstubAllGlobals(); });

  it("filters dates and unassociated sources, then restores them on remount", async () => {
    const view = render(<CardPreviewModule />);
    await screen.findByRole("article", { name: "信息卡详情" });
    fireEvent.change(screen.getByLabelText("发布截止日期"), { target: { value: "2026-09-16" } });
    await waitFor(() => expect(cardPreviewApi.fetchCardPreviewFacets).toHaveBeenLastCalledWith(
      expect.any(AbortSignal), { published_since: "", published_until: "2026-09-16" }));
    fireEvent.click(screen.getByText("按来源统计（2 项）"));
    fireEvent.click(screen.getByRole("button", { name: /未关联来源 RSS 42 张/ }));
    await waitFor(() => expect(listMock).toHaveBeenCalledWith(expect.objectContaining({
      source_id: "__none__", platform: "rss", published_until: "2026-09-16", offset: 0,
    }), expect.anything()));
    expect(new URLSearchParams(window.location.search).get("source_id")).toBe("__none__");
    expect(new URLSearchParams(window.location.search).get("published_until")).toBe("2026-09-16");
    view.unmount();
    render(<CardPreviewModule />);
    expect(screen.getByLabelText("发布截止日期")).toHaveValue("2026-09-16");
    expect(screen.getByLabelText("来源")).toHaveValue('["rss","__none__"]');
    fireEvent.change(screen.getByLabelText("发布起始日期"), { target: { value: "2026-09-01" } });
    await waitFor(() => expect(listMock).toHaveBeenCalledWith(expect.objectContaining({
      source_id: "__none__", platform: "rss", published_since: "2026-09-01", published_until: "2026-09-16",
    }), expect.anything()));
    fireEvent.change(screen.getByLabelText("来源"), { target: { value: "" } });
    await waitFor(() => expect(new URLSearchParams(window.location.search).has("source_id")).toBe(false));
    fireEvent.click(screen.getByRole("radio", { name: "YouTube" }));
    await waitFor(() => expect(listMock).toHaveBeenCalledWith(expect.objectContaining({ platform: "youtube", source_id: "" }), expect.anything()));
  });

  it("shows source-stat errors and recovers on retry", async () => {
    vi.mocked(cardPreviewApi.fetchCardPreviewFacets).mockRejectedValueOnce(new Error("offline"));
    render(<CardPreviewModule />);
    fireEvent.click(screen.getByText("按来源统计"));
    expect(await screen.findByRole("alert")).toHaveTextContent("来源统计加载失败");
    fireEvent.click(screen.getByRole("button", { name: "重试统计" }));
    expect(await screen.findByRole("button", { name: /未关联来源 RSS 42 张/ })).toBeInTheDocument();
  });

  it("shows the list and opens the first card in the reading pane", async () => {
    render(<CardPreviewModule />);
    expect(await screen.findByRole("article", { name: "信息卡详情" })).toBeInTheDocument();
    expect(within(screen.getByRole("article", { name: "信息卡详情" })).getByRole("heading", { name: "第一张信息卡" })).toBeInTheDocument();
    expect(screen.getByText("共 2 条")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "2 张处理中" })).toBeInTheDocument();
    expect(screen.queryByText("沉浸浏览")).not.toBeInTheDocument();
    expect(listMock).toHaveBeenCalledWith(expect.objectContaining({ limit: 30, offset: 0, sort: "published_desc" }), expect.anything());
    expect(listMock).toHaveBeenCalledTimes(1);
  });

  it("opens the selected card in a reading dialog and closes with Escape", async () => {
    render(<CardPreviewModule />);
    fireEvent.click(await screen.findByRole("button", { name: "阅读" }));
    const dialog = screen.getByRole("dialog", { name: "第一张信息卡" });
    expect(within(dialog).getByRole("heading", { level: 1, name: "第一张信息卡" })).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "新标签页打开" })).toHaveAttribute("href", "/r/card/card-1");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "第一张信息卡" })).not.toBeInTheDocument();
  });

  it("fetches fresh details for cached cards after refresh", async () => {
    render(<CardPreviewModule />);
    await screen.findByText("这是用户可读的正文。");
    fireEvent.click(screen.getByRole("button", { name: /第二张信息卡/ }));
    await waitFor(() => expect(detailMock).toHaveBeenCalledTimes(2));
    await screen.findByRole("article", { name: "信息卡详情" });
    fireEvent.click(screen.getByRole("button", { name: /第一张信息卡/ }));
    await screen.findByRole("article", { name: "信息卡详情" });
    expect(detailMock).toHaveBeenCalledTimes(2);
    // Media and evaluations can change without changing the list's card version.
    detailMock.mockImplementation(async (cardId) => {
      const value = detail(cardId);
      return { ...value, body_blocks: [{ ...value.body_blocks[0], content: `${cardId} 刷新后的正文` }] };
    });
    fireEvent.click(screen.getByRole("button", { name: "刷新卡片" }));
    await screen.findByText("card-1 刷新后的正文");
    fireEvent.click(screen.getByRole("button", { name: /第二张信息卡/ }));
    await screen.findByText("card-2 刷新后的正文");
    expect(screen.queryByText("这是用户可读的正文。")).not.toBeInTheDocument();
  });

  it("selects a row, updates the URL, and keeps detail inline", async () => {
    render(<CardPreviewModule />);
    await screen.findByRole("article", { name: "信息卡详情" });
    fireEvent.click(screen.getByRole("button", { name: /第二张信息卡/ }));
    await waitFor(() => expect(detailMock).toHaveBeenCalledWith("card-2", expect.anything()));
    await waitFor(() => expect(window.location.search).toContain("card_id=card-2"));
    expect(screen.queryByRole("dialog", { name: "信息卡详情" })).not.toBeInTheDocument();
  });

  it("filters with debounce and changes sort and platform", async () => {
    render(<CardPreviewModule />);
    await screen.findByRole("article", { name: "信息卡详情" });
    fireEvent.change(screen.getByRole("searchbox", { name: "搜索信息卡" }), { target: { value: "正文" } });
    await waitFor(() => expect(listMock).toHaveBeenCalledWith(expect.objectContaining({ query: "正文" }), expect.anything()));
    fireEvent.change(screen.getByRole("combobox", { name: "排序" }), { target: { value: "updated_desc" } });
    fireEvent.click(screen.getByRole("radio", { name: "小红书" }));
    await waitFor(() => expect(listMock).toHaveBeenCalledWith(expect.objectContaining({ platform: "xiaohongshu", sort: "updated_desc" }), expect.anything()));
    expect(window.location.search).toContain("sort=updated_desc");
    expect(screen.getByRole("button", { name: "2 张处理中" })).toBeInTheDocument();
    expect(listMock.mock.calls.every(([filters]) => filters?.limit === 30)).toBe(true);
  });

  it("keeps processing cards non-openable", async () => {
    const processing = { ...page.items[0], completeness_status: "processing" as const };
    listMock.mockImplementation(async (filters) => filters?.status === "processing"
      ? { items: [processing], total: 1, processing_total: 1, limit: 30, offset: 0 }
      : { ...page, processing_total: 1 });
    render(<CardPreviewModule />);
    fireEvent.click(await screen.findByRole("button", { name: "1 张处理中" }));
    expect(await screen.findByText("等待定稿")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /第一张信息卡/ })).toBeDisabled();
    expect(window.location.search).toContain("status=processing");
  });

  it("enables recommendation filtering with relevance sorting and restores the default on close", async () => {
    render(<CardPreviewModule />);
    await screen.findByRole("article", { name: "信息卡详情" });
    const toggle = screen.getByRole("switch", { name: "只看已推荐" });
    expect(screen.queryByRole("option", { name: "按相关度" })).not.toBeInTheDocument();
    fireEvent.click(toggle);
    await waitFor(() => expect(listMock).toHaveBeenCalledWith(
      expect.objectContaining({ recommended: true, sort: "relevance_desc", offset: 0 }), expect.anything(),
    ));
    expect(screen.getByRole("combobox", { name: "排序" })).toHaveValue("relevance_desc");
    expect(window.location.search).toContain("recommended=1");
    expect(window.location.search).toContain("sort=relevance_desc");
    fireEvent.click(toggle);
    await waitFor(() => expect(listMock).toHaveBeenCalledWith(
      expect.objectContaining({ recommended: false, sort: "published_desc", offset: 0 }), expect.anything(),
    ));
    expect(screen.getByRole("combobox", { name: "排序" })).toHaveValue("published_desc");
    expect(window.location.search).not.toContain("recommended=");
    expect(window.location.search).not.toContain("sort=");
    expect(screen.queryByRole("option", { name: "按相关度" })).not.toBeInTheDocument();
  });

  it("counts recommendation and its automatic relevance sort as one mobile filter", async () => {
    vi.stubGlobal("matchMedia", vi.fn().mockImplementation((media: string) => ({
      matches: true, media, addEventListener: vi.fn(), removeEventListener: vi.fn(),
    })));
    render(<CardPreviewModule />);
    fireEvent.click(await screen.findByRole("button", { name: "筛选（0）" }));
    fireEvent.click(screen.getByRole("switch", { name: "只看已推荐" }));
    await waitFor(() => expect(listMock).toHaveBeenCalledWith(
      expect.objectContaining({ recommended: true, sort: "relevance_desc" }), expect.anything(),
    ));
    expect(screen.getByRole("combobox", { name: "排序" })).toHaveValue("relevance_desc");
    expect(screen.getByRole("button", { name: "筛选（1）" })).toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox", { name: "排序" }), { target: { value: "updated_desc" } });
    expect(screen.getByRole("button", { name: "筛选（2）" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("switch", { name: "只看已推荐" }));
    expect(screen.getByRole("button", { name: "筛选（1）" })).toBeInTheDocument();
  });

  it("restores recommended and relevance sorting after refresh and preserves a time sort when closing", async () => {
    window.history.replaceState({}, "", "/?menu=cards&recommended=1&sort=relevance_desc");
    render(<CardPreviewModule />);
    await waitFor(() => expect(listMock).toHaveBeenCalledWith(
      expect.objectContaining({ recommended: true, sort: "relevance_desc" }), expect.anything(),
    ));
    expect(screen.getByRole("switch", { name: "只看已推荐" })).toBeChecked();
    fireEvent.change(screen.getByRole("combobox", { name: "排序" }), { target: { value: "updated_desc" } });
    fireEvent.click(screen.getByRole("switch", { name: "只看已推荐" }));
    await waitFor(() => expect(listMock).toHaveBeenCalledWith(
      expect.objectContaining({ recommended: false, sort: "updated_desc" }), expect.anything(),
    ));
  });

  it("renders recommended, rejected, and unevaluated items", async () => {
    const evaluation = { quality: 90, relevance: null, passed_quality: true, attribution_kind: "project",
      attribution_ref: "产品研究/长归属引用", content_type: "tool", suggested_action: "read",
      summary: "值得看的评估摘要", relation_note: "支持产品研究", evaluated_at: "2026-10-07T10:00:00Z",
      prompt_version: "rubric_v1", profile_version: 1, model: "external-agent" };
    listMock.mockImplementation(async (filters) => filters?.status === "processing"
      ? { items: [], total: 0, processing_total: 0, limit: 30, offset: 0 }
      : { ...page, total: 3, items: [
        { ...page.items[0], evaluation },
        { ...page.items[1], evaluation: { ...evaluation, quality: 40, passed_quality: false, summary: "不应展开的摘要" }, summary_excerpt: "也不显示旧摘要" },
        { ...page.items[0], card_id: "card-3", title: "未评估信息卡", evaluation: null, summary_excerpt: "原有摘要" },
      ] });
    render(<CardPreviewModule />);
    const list = within(screen.getByLabelText("信息卡列表"));
    expect(await list.findByText("值得看的评估摘要")).toBeInTheDocument();
    expect(list.getByText("与你的关系：支持产品研究")).toBeInTheDocument();
    expect(list.getByText(/质量 90 · 相关 —/)).toHaveTextContent("归属：项目 · 产品研究/长归属引用 · 工具/产品 · AI 建议：精读");
    expect(list.getByText("未推荐 · 质量 40")).toBeInTheDocument();
    expect(list.queryByText("不应展开的摘要")).not.toBeInTheDocument();
    expect(list.queryByText(/也不显示旧摘要/)).not.toBeInTheDocument();
    expect(list.getByText("AI：原有摘要")).toBeInTheDocument();
    expect(list.getAllByText(/与你的关系：/)).toHaveLength(1);
  });

  it("restores card and capture deep links and opens asset preview", async () => {
    window.history.replaceState({}, "", "/?menu=cards&capture_id=cap-1&asset_id=image-1");
    render(<CardPreviewModule />);
    await waitFor(() => expect(captureMock).toHaveBeenCalledWith("cap-1", expect.anything()));
    expect(await screen.findByRole("article", { name: "信息卡详情" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("dialog", { name: "图片预览" })).toBeInTheDocument());
  });

  it("restores filters from the URL", async () => {
    window.history.replaceState({}, "", "/?menu=cards&q=城市&platform=xiaohongshu&completeness=partial&sort=updated_desc&card_id=card-2");
    render(<CardPreviewModule />);
    await waitFor(() => expect(listMock).toHaveBeenCalledWith(expect.objectContaining({ query: "城市", platform: "xiaohongshu", completeness: "partial", sort: "updated_desc" }), expect.anything()));
    await waitFor(() => expect(detailMock).toHaveBeenCalledWith("card-2", expect.anything()));
  });

  it("shows a deep-linked card outside the first page in the list and reading pane", async () => {
    window.history.replaceState({}, "", "/?menu=cards&card_id=card-99");
    detailMock.mockImplementation(async (cardId) => cardId === "card-99"
      ? { ...detail(cardId), card: { ...detail(cardId).card, title: "第九十九张信息卡" } }
      : detail(cardId));
    render(<CardPreviewModule />);
    await waitFor(() => expect(detailMock).toHaveBeenCalledWith("card-99", expect.anything()));
    expect(await screen.findByRole("article", { name: "信息卡详情" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /第九十九张信息卡/ })).toHaveAttribute("aria-current", "true");
  });

  it("appends the next thirty-card page when the sentinel enters view", async () => {
    let intersect: IntersectionObserverCallback = () => undefined;
    vi.stubGlobal("IntersectionObserver", class {
      constructor(callback: IntersectionObserverCallback) { intersect = callback; }
      observe() { /* controlled by this test */ }
      disconnect() { /* controlled by this test */ }
    });
    const many = Array.from({ length: 31 }, (_, index) => ({ ...page.items[0], card_id: `card-${index}`, title: `卡片 ${index}` }));
    listMock.mockImplementation(async (filters) => filters?.status === "processing"
      ? { items: [], total: 0, processing_total: 0, limit: 30, offset: 0 }
      : { items: many.slice(filters?.offset ?? 0, (filters?.offset ?? 0) + 30), total: 31, processing_total: 0, limit: 30, offset: filters?.offset ?? 0 });
    render(<CardPreviewModule />);
    expect(await screen.findByRole("heading", { name: "卡片 29" })).toBeInTheDocument();
    await act(async () => { intersect([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver); });
    expect(await screen.findByRole("heading", { name: "卡片 30" })).toBeInTheDocument();
    expect(listMock).toHaveBeenCalledWith(expect.objectContaining({ offset: 30, limit: 30 }), undefined);
  });

  it("keeps historical summaries above original text", async () => {
    const base = detail();
    detailMock.mockImplementation(async (cardId) => cardId === "card-1" ? {
      ...base,
      body_blocks: [...base.body_blocks,
        { ...base.body_blocks[0], block_id: "summary-1", layer: "intelligence", block_kind: "summary", content: "这是一段中文解读。", raw_payload: { purpose: "ai_interpretation" } },
        { ...base.body_blocks[0], block_id: "translation-1", layer: "intelligence", block_kind: "translation", content: "历史中文译文。", raw_payload: { purpose: "body_translation" } },
      ],
    } : detail(cardId));
    render(<CardPreviewModule />);
    const interpretation = await screen.findByRole("region", { name: "历史摘要" });
    const original = screen.getByRole("region", { name: "原文" });
    expect(interpretation).toHaveTextContent("这是一段中文解读。");
    expect(interpretation.compareDocumentPosition(original) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const languages = screen.getByRole("radiogroup", { name: "正文语言" });
    fireEvent.click(within(languages).getByRole("radio", { name: "中文翻译" }));
    expect(original).toHaveTextContent("历史中文译文。");
    expect(original).not.toHaveTextContent("这是用户可读的正文。");
    fireEvent.click(within(languages).getByRole("radio", { name: "原文" }));
    expect(original).toHaveTextContent("这是用户可读的正文。");
  });

  it.each([true, false])("shows evaluation or the unevaluated message: %s", async (evaluated) => {
    const base = detail();
    detailMock.mockResolvedValue({ ...base, evaluation: evaluated ? {
      quality: 40, relevance: null, passed_quality: false, attribution_kind: null,
      attribution_ref: null, content_type: "tool", suggested_action: "read",
      summary: "详情评估摘要", relation_note: "支持研究", evaluated_at: "2026-10-19T00:00:00Z",
      prompt_version: "rubric_v1", profile_version: 3, model: "external-agent",
    } : null });
    render(<CardPreviewModule />);
    const region = await screen.findByRole("region", { name: "评估" });
    if (evaluated) {
      expect(region).toHaveTextContent("详情评估摘要");
      expect(region).toHaveTextContent("与你的关系：支持研究");
      expect(region).toHaveTextContent("质量 40 · 相关 —");
      expect(region).toHaveTextContent("rubric rubric_v1 · 画像 3 · evaluator external-agent");
      expect(region).not.toHaveTextContent("尚未评估");
    } else {
      expect(region).toHaveTextContent("尚未评估");
    }
    expect(screen.queryByText(/正在生成|熔断恢复|尚未生成/)).not.toBeInTheDocument();
    expect(screen.queryByRole("radiogroup", { name: "正文语言" })).not.toBeInTheDocument();
  });

  it.each([false, true])("shows the media gap with existing attachments: %s", async (hasAttachments) => {
    const base = detail();
    detailMock.mockResolvedValue({ ...base,
      image_assets: hasAttachments ? base.image_assets : [],
      video_assets: hasAttachments ? base.video_assets : [],
      media_gap: { reason: "douyin_browser_no_media_resources", status: "blocked" },
    });
    render(<CardPreviewModule />);
    const media = await screen.findByRole("region", { name: "媒体" });
    expect(media).toHaveTextContent(`${hasAttachments ? "部分媒体未下载" : "媒体未下载"} · 页面里没找到视频资源`);
    expect(within(media).getByRole("link", { name: "去原平台查看" })).toHaveAttribute("href", base.card.source_url);
  });

  it("omits the gap notice and empty media section when media_gap is null", async () => {
    detailMock.mockResolvedValue({ ...detail(), image_assets: [], video_assets: [], media_gap: null });
    render(<CardPreviewModule />);
    await screen.findByRole("article", { name: "信息卡详情" });
    expect(screen.queryByRole("region", { name: "媒体" })).not.toBeInTheDocument();
    expect(screen.queryByRole("note", { name: "媒体下载提示" })).not.toBeInTheDocument();
  });

  it("opens media from the reader and switches to the next attachment", async () => {
    render(<CardPreviewModule />);
    fireEvent.click(await screen.findByRole("button", { name: "预览图片 image-1" }));
    expect(screen.getByRole("dialog", { name: "图片预览" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "下一项媒体" }));
    expect(screen.getByRole("dialog", { name: "视频预览" })).toBeInTheDocument();
  });

  it("moves selection with keyboard shortcuts", async () => {
    render(<CardPreviewModule />);
    await screen.findByRole("article", { name: "信息卡详情" });
    const list = screen.getByLabelText("信息卡列表");
    list.focus();
    fireEvent.keyDown(list, { key: "j" });
    await waitFor(() => expect(window.location.search).toContain("card_id=card-2"));
    fireEvent.keyDown(list, { key: "ArrowUp" });
    await waitFor(() => expect(window.location.search).toContain("card_id=card-1"));
  });
});
