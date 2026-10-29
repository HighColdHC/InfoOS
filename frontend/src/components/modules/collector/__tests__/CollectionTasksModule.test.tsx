import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fetchCardPreviewDetail } from "../../../../lib/api/cardPreview";
import { fetchSourceRuns, fetchSourceRunTrace, fetchTaskTrackingPage, retrySourceRunTask } from "../../../../lib/api/sourceRuns";
import type { CardPreviewDetail } from "../../../../lib/types/cardPreview";
import { createResearchRun, fetchResearchCapabilities, fetchResearchRun, fetchResearchRuns } from "../../../../lib/api/research";
import { CollectionTasksModule } from "../CollectionTasksModule";
import { traceFixture } from "../../taskManager/sourceRunTrace/__tests__/sourceRunTraceTestFixtures";
import { dateTime } from "../../taskManager/sourceRunTrace/sourceRunTraceViewModel";
import { ToastProvider } from "../../../ui/Toast";

vi.mock("../../../../lib/api/sourceRuns", () => ({ fetchSourceRuns: vi.fn(), fetchSourceRunTrace: vi.fn(), fetchTaskTrackingPage: vi.fn(), retrySourceRunTask: vi.fn() }));
vi.mock("../../../../lib/api/cardPreview", () => ({ fetchCardPreviewDetail: vi.fn() }));
vi.mock("../../../../lib/api/research", () => ({ createResearchRun: vi.fn(), fetchResearchCapabilities: vi.fn(), fetchResearchRun: vi.fn(), fetchResearchRuns: vi.fn() }));

const researchCardDetail: CardPreviewDetail = {
  opportunity: null, knowledge_cards: [],
  media_gap: null,
  card: {
    card_id: "card-1", title: "调研预览信息卡", author: "作者甲", platform: "scys",
    first_seen_at: "2026-07-16T08:31:00Z", published_at: "2026-07-16T08:30:00Z", captured_at: "2026-07-16T08:31:00Z",
    completeness_status: "complete", status: "active", version: 1, updated_at: "2026-07-16T09:00:00Z",
    content_hash: "sha256:research", processor_version: "collector/1.0",
    source_label: "生财有术", source_url: "https://scys.com/article/1",
    raw_item_id: "raw-1", source_run_id: "run-xhs-001", attachment_count: 0,
    engagement_metrics: {},
  },
  evaluation: null,
  body_blocks: [],
  comment_blocks: [],
  image_assets: [{
    asset_id: "image-1", card_id: "card-1", asset_kind: "image", mime_type: "image/jpeg",
    status: "ready", size_bytes: 1024, asset_url: "/api/card-preview/assets/image-1",
  }],
  video_assets: [{
    asset_id: "video-1", card_id: "card-1", asset_kind: "video", mime_type: "video/mp4",
    status: "ready", size_bytes: 2048, asset_url: "/api/card-preview/assets/video-1",
  }],
  missing_reasons: [],
  trace: { source_run_id: "run-xhs-001" },
};

const page = {
  total_exact: true, counts_exact: true,
  summary: { last_24h: 12, completed: 8, attention: 2, active: 2 },
  queue_summary: {
    public_fetch: { pending: 4, running: 1, failed: 0, blocked: 0, overdue: 1, oldest_wait_seconds: 3661, running_worker_ids: ["worker-public-1"], running_worker_count: 1 },
    browser_fetch: { pending: 0, running: 0, failed: 0, blocked: 0, overdue: 0, oldest_wait_seconds: null, running_worker_ids: [], running_worker_count: 0 },
  },
  items: [{
    source_run_id: "run-xhs-001", root_task_id: "task-root-001", source_id: "source-xhs-001", source_name: "小红书 · 产品观察",
    platform: "xhs", status: "attention", execution_bucket: "due", trigger: "manual", started_at: "2026-07-16T08:00:00Z", duration_ms: 81200,
    fetch_result: { total_links: 10, dedup_skipped: 3, pending_items: 7, raw_item_count: 7 }, queue_name: "public_fetch", priority: 80, deadline_at: "2026-07-16T08:30:00Z", wait_seconds: 3661, overdue: true, claimed_by: "worker-public-1",
    card_progress: { total: 7, complete: 6, partial: 1, failed: 0 }, blocking_reason: "platform_not_logged_in",
  }], total: 16, next_offset: 15,
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((nextResolve, nextReject) => {
    resolve = nextResolve;
    reject = nextReject;
  });
  return { promise, resolve, reject };
}

describe("CollectionTasksModule", () => {
  const renderModule = () => render(<ToastProvider><CollectionTasksModule /></ToastProvider>);
  beforeEach(() => {
    window.history.replaceState({}, "", "/?menu=tasks");
    vi.clearAllMocks();
    vi.mocked(fetchSourceRuns).mockResolvedValue(page);
    vi.mocked(fetchSourceRunTrace).mockResolvedValue(traceFixture);
    vi.mocked(fetchCardPreviewDetail).mockResolvedValue(researchCardDetail);
    vi.mocked(fetchTaskTrackingPage).mockResolvedValue({
      items: [{
        task_id: "task-export-1", task_type: "data_export", status: "pending",
        queue_name: "maintenance_io", priority: 90,
        deadline_at: "2026-07-17T08:00:00Z", wait_seconds: 120,
        overdue: false, claimed_by: null,
      }],
      total: 1, limit: 20, offset: 0, next_offset: null,
    });
    vi.mocked(retrySourceRunTask).mockResolvedValue({ task_id: "task-video-1", attempt: 2, status: "pending" });
    vi.mocked(fetchResearchCapabilities).mockResolvedValue([
      { platform: "x", label: "X", research: { sort_options: ["latest"], default_sort: "latest", status: "available", max_items_limit: 100, supports_date_range: true, requires_source_selection: false } },
      { platform: "rss", label: "RSS", research: { sort_options: ["latest"], default_sort: "latest", status: "conditional", max_items_limit: 50, supports_date_range: false, requires_source_selection: true, limitation: "需要选择已有来源" } },
      { platform: "producthunt", label: "Product Hunt", research: { sort_options: ["latest"], default_sort: "latest", status: "unavailable", max_items_limit: 0, supports_date_range: false, requires_source_selection: false, limitation: "尚未配置访问授权" } },
    ]);
    vi.mocked(fetchResearchRuns).mockResolvedValue({ ok: true, research_runs: [{ research_run_id: "research-1", name: "已有调研", status: "completed", keywords: ["AI"] }] });
    vi.mocked(createResearchRun).mockResolvedValue({ ok: true, replayed: false, research_run: { research_run_id: "research-new", name: "AI 出海", status: "queued", keywords: ["AI", "出海"] } });
    vi.mocked(fetchResearchRun).mockResolvedValue({ research_run: { research_run_id: "research-new", name: "AI 出海", status: "queued", keywords: ["AI"] }, platform_runs: [], coverage: [], item_page: { items: [], next_cursor: null } });
  });

  it("uses total_exact for pagination independently of the scanned summary", async () => {
    const user = userEvent.setup();
    vi.mocked(fetchSourceRuns).mockResolvedValue({ ...page, total_exact: false, counts_exact: false });
    renderModule();
    expect(await screen.findByText("已扫描至少 16 条 · 第 1 页")).toBeInTheDocument();
    expect(screen.getByText("已完成（已扫描）")).toBeInTheDocument();
    expect(screen.getByText("≥8")).toBeInTheDocument();
    vi.mocked(fetchSourceRuns).mockResolvedValue({ ...page, total_exact: true, counts_exact: false });
    await user.click(screen.getByRole("button", { name: "刷新当前页" }));
    expect(await screen.findByText("共 16 条 · 第 1 页")).toBeInTheDocument();
    expect(screen.getByText("已完成（已扫描）")).toBeInTheDocument();
  });

  it("submits a multi-keyword research batch, clearly exposes unavailable platforms, and opens queued detail", async () => {
    const user = userEvent.setup();
    renderModule();
    await user.click(screen.getByRole("tab", { name: "调研任务" }));
    await screen.findByText("发起调研");
    expect(screen.getByLabelText("Product Hunt")).toBeDisabled();
    await user.type(screen.getByLabelText("调研名称"), "AI 出海");
    await user.type(screen.getByLabelText("调研关键词"), "AI, 出海");
    await user.click(screen.getByLabelText("X"));
    await user.click(screen.getByRole("button", { name: "提交调研任务" }));
    await waitFor(() => expect(createResearchRun).toHaveBeenCalledWith(expect.objectContaining({ name: "AI 出海", keywords: ["AI", "出海"], platforms: [expect.objectContaining({ platform: "x", sort: "latest" })] }), expect.any(String)));
    expect(await screen.findByRole("region", { name: "调研批次详情" })).toHaveTextContent("已排队");
    expect(screen.getByText("提交成功仅代表已排队，不代表采集或信息卡已完成。")).toBeInTheDocument();
  });

  it("requires RSS source IDs before submitting its conditional research mode", async () => {
    const user = userEvent.setup();
    renderModule();
    await user.click(screen.getByRole("tab", { name: "调研任务" }));
    await screen.findByText("发起调研");
    await user.type(screen.getByLabelText("调研名称"), "RSS 调研");
    await user.type(screen.getByLabelText("调研关键词"), "行业报告");
    await user.click(screen.getByLabelText("RSS"));
    await user.click(screen.getByRole("button", { name: "提交调研任务" }));
    expect(await screen.findByText("RSS 需要填写已有来源 ID。")).toHaveAttribute("role", "alert");
    expect(createResearchRun).not.toHaveBeenCalled();
  });

  it("initializes the Source Run query from a capture deep link", async () => {
    window.history.replaceState({}, "", "/?menu=tasks&capture_id=cap-deep-link");
    renderModule();
    await waitFor(() => expect(fetchSourceRuns).toHaveBeenCalledWith(
      expect.objectContaining({ query: "cap-deep-link", offset: 0 }),
      expect.anything(),
    ));
    expect(screen.getByLabelText("搜索来源运行")).toHaveValue("cap-deep-link");
  });

  it("renders queue overview, blocking login link, queue-aware Source Run table, and does not trace eagerly", async () => {
    renderModule();
    expect(await screen.findByText("最近 24 小时")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("平台登录已失效");
    expect(screen.getByRole("link", { name: "处理 xhs 登录" })).toHaveAttribute("href", "?menu=environment&platform=xhs");
    const platforms = screen.getByLabelText("平台筛选");
    for (const label of ["X", "YouTube", "Reddit", "小红书", "抖音", "生财有术"]) {
      expect(within(platforms).getByRole("option", { name: label })).toBeInTheDocument();
    }
    const rowElement = screen.getByRole("row", { name: /小红书 · 产品观察/ });
    const row = within(rowElement);
    expect(row.getByText("链接 10 · 去重 3 · 待抓取 7")).toBeInTheDocument();
    expect(rowElement).toHaveTextContent("优先级 80 · 等待 1 时 1 分");
    expect(rowElement).toHaveTextContent("已逾期 · worker-public-1");
    expect(fetchSourceRunTrace).not.toHaveBeenCalled();
    expect(fetchTaskTrackingPage).not.toHaveBeenCalled();
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: "任务队列" }));
    const queues = screen.getByRole("region", { name: "队列概览" });
    expect(within(queues).getByText("公开采集")).toBeInTheDocument();
    expect(within(queues).getByText("逾期 1")).toBeInTheDocument();
    expect(within(queues).getByLabelText("当前认领 worker 1 个：worker-public-1")).toBeInTheDocument();
    expect(within(queues).getByText("暂无任务被认领")).toBeInTheDocument();
    const allTasks = screen.getByRole("region", { name: "全部任务队列追踪" });
    expect(await within(allTasks).findByText("data_export")).toBeInTheDocument();
    expect(within(allTasks).getByText("维护与 I/O")).toBeInTheDocument();
    expect(fetchSourceRunTrace).not.toHaveBeenCalled();
  });

  it("collapses repeated blocked runs for one platform into one actionable message", async () => {
    const repeatedItems = Array.from({ length: 3 }, (_, index) => ({
      ...page.items[0],
      source_run_id: `run-xhs-${index + 1}`,
    }));
    vi.mocked(fetchSourceRuns).mockResolvedValueOnce({
      ...page,
      items: repeatedItems,
      total: repeatedItems.length,
      next_offset: null,
    });

    renderModule();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent.match(/平台登录已失效/g)).toHaveLength(1);
    expect(within(alert).getAllByRole("link", { name: "处理 xhs 登录" })).toHaveLength(1);
  });

  it("preserves filters and page when refreshing, then loads trace only from the trace action", async () => {
    const user = userEvent.setup();
    renderModule();
    await screen.findByRole("button", { name: "追踪 小红书 · 产品观察" });
    await user.type(screen.getByLabelText("搜索来源运行"), "产品");
    await user.selectOptions(screen.getByLabelText("平台筛选"), "xiaohongshu");
    await user.selectOptions(screen.getByLabelText("状态筛选"), "attention");
    await user.selectOptions(screen.getByLabelText("执行范围筛选"), "upcoming");
    await user.click(screen.getByRole("button", { name: "查询" }));
    await user.click(screen.getByRole("button", { name: "下一页" }));
    await user.click(screen.getByRole("button", { name: "刷新当前页" }));
    await waitFor(() => expect(fetchSourceRuns).toHaveBeenLastCalledWith(expect.objectContaining({ query: "产品", platform: "xiaohongshu", status: "attention", executionScope: "upcoming", offset: 15, limit: 15 }), expect.anything()));
    expect(fetchSourceRunTrace).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "追踪 小红书 · 产品观察" }));
    expect(fetchSourceRunTrace).toHaveBeenCalledWith("run-xhs-001", expect.anything());
    expect(await screen.findByRole("dialog", { name: "来源运行追踪" })).toBeInTheDocument();
  });

  it("switches execution scope from the first page and shows the persisted execution bucket", async () => {
    const user = userEvent.setup();
    renderModule();
    expect(await screen.findByText("已到期")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "下一页" }));
    await user.selectOptions(screen.getByLabelText("执行范围筛选"), "executed");
    await user.click(screen.getByRole("button", { name: "查询" }));

    await waitFor(() => expect(fetchSourceRuns).toHaveBeenLastCalledWith(expect.objectContaining({ executionScope: "executed", offset: 0 }), expect.anything()));
  });

  it("switches to research runs, resets pagination, and requests the research task view", async () => {
    const user = userEvent.setup();
    vi.mocked(fetchSourceRuns).mockResolvedValue({
      ...page,
      items: [{ ...page.items[0], source_type: "keyword", task_view: "research", trace_available: false }],
      total: 1,
      next_offset: null,
    });
    renderModule();
    await user.click(await screen.findByRole("button", { name: "下一页" }));
    await user.click(screen.getByRole("tab", { name: "调研任务" }));
    await waitFor(() => expect(fetchSourceRuns).toHaveBeenLastCalledWith(expect.objectContaining({ taskView: "research", offset: 0 }), expect.anything()));
    expect(await screen.findByRole("columnheader", { name: "本次检索语料" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "等待执行" })).toBeDisabled();
    expect(screen.queryByRole("region", { name: "全部任务队列追踪" })).not.toBeInTheDocument();
  });

  it("opens the research detail in the existing trace dialog", async () => {
    const user = userEvent.setup();
    const researchTrace = {
      ...traceFixture,
      items: traceFixture.items.map((item, index) => ({
        ...item,
        association: "associated_with_this_run",
        card_projection_provenance: index === 0
          ? "created_or_updated_by_this_pipeline"
          : index === 1 ? "reused_existing_pipeline" : "provenance_unavailable",
        finalize: { ...item.finalize, status: "success" },
      })),
    };
    vi.mocked(fetchSourceRuns).mockResolvedValue({
      ...page,
      items: [{ ...page.items[0], source_type: "keyword", task_view: "research", trace_available: true }],
      total: 1,
      next_offset: null,
    });
    vi.mocked(fetchSourceRunTrace).mockResolvedValue(researchTrace);
    renderModule();
    await user.click(await screen.findByRole("tab", { name: "调研任务" }));
    await user.click(await screen.findByRole("button", { name: "查看调研 小红书 · 产品观察" }));
    expect(await screen.findByRole("dialog", { name: "调研详情" })).toBeInTheDocument();
    expect(screen.getAllByText("本次检索语料").length).toBeGreaterThan(0);
    expect(screen.getByRole("region", { name: "本次运行关联的信息卡" })).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "调研卡片处理进度" })).toHaveAttribute("value", "100");
    expect(screen.getByText("技术追踪证据")).toBeInTheDocument();
    expect(screen.getByText("本次运行创建或更新")).toBeInTheDocument();
    expect(screen.getByText("复用已有信息卡链路")).toBeInTheDocument();
    expect(screen.getByText("关联证据不可用")).toBeInTheDocument();

    await user.click(screen.getAllByRole("button", { name: "预览信息卡" })[0]);
    await waitFor(() => expect(fetchCardPreviewDetail).toHaveBeenCalledWith("card-1", expect.anything()));
    expect(await screen.findByRole("heading", { name: "调研预览信息卡" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "预览图片 image-1" }));
    expect(screen.getByRole("dialog", { name: "图片预览" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "调研详情" })).not.toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "图片预览" })).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "调研详情" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "调研预览信息卡" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "预览视频 video-1" }));
    expect(screen.getByRole("dialog", { name: "视频预览" })).toBeInTheDocument();
    await user.click(within(screen.getByRole("dialog", { name: "视频预览" })).getByRole("button", { name: "关闭对话框" }));
    await user.click(screen.getByRole("button", { name: "返回调研详情" }));
    expect(screen.getByRole("region", { name: "本次运行关联的信息卡" })).toBeInTheDocument();
  });

  it("keeps a card with an ID non-previewable until finalize has produced a stable card", async () => {
    const user = userEvent.setup();
    const processingItem = {
      ...traceFixture.items[0],
      finalize: { ...traceFixture.items[0].finalize, status: "pending", completeness: "unavailable" },
    };
    vi.mocked(fetchSourceRuns).mockResolvedValue({
      ...page,
      items: [{ ...page.items[0], source_type: "keyword", task_view: "research", trace_available: true }],
      total: 1,
      next_offset: null,
    });
    vi.mocked(fetchSourceRunTrace).mockResolvedValue({
      ...traceFixture,
      items: [processingItem],
      item_page: { items: [processingItem], item_total: 1, truncated: false, next_cursor: null },
    });
    renderModule();
    await user.click(await screen.findByRole("tab", { name: "调研任务" }));
    await user.click(await screen.findByRole("button", { name: "查看调研 小红书 · 产品观察" }));
    expect(await screen.findByText("信息卡仍在处理，暂不可预览")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "预览信息卡" })).toBeDisabled();
    expect(fetchCardPreviewDetail).not.toHaveBeenCalled();
  });

  it("does not let a late card response from one research run overwrite the next dialog", async () => {
    const user = userEvent.setup();
    const lateDetail = deferred<CardPreviewDetail>();
    const runs = [
      { ...page.items[0], source_run_id: "run-a", source_name: "调研 A", source_type: "keyword", task_view: "research" as const, trace_available: true },
      { ...page.items[0], source_run_id: "run-b", source_name: "调研 B", source_type: "keyword", task_view: "research" as const, trace_available: true },
    ];
    const stableItem = { ...traceFixture.items[0], finalize: { ...traceFixture.items[0].finalize, status: "success" } };
    const traceFor = (runId: string) => ({
      ...traceFixture,
      run: { ...traceFixture.run, source_run_id: runId, source_name: runId === "run-a" ? "调研 A" : "调研 B" },
      items: [{ ...stableItem, card_id: runId === "run-a" ? "card-a" : "card-b", title: runId === "run-a" ? "卡片 A" : "卡片 B" }],
      item_page: {
        items: [{ ...stableItem, card_id: runId === "run-a" ? "card-a" : "card-b", title: runId === "run-a" ? "卡片 A" : "卡片 B" }],
        item_total: 1, truncated: false, next_cursor: null,
      },
    });
    vi.mocked(fetchSourceRuns).mockResolvedValue({ ...page, items: runs, total: 2, next_offset: null });
    vi.mocked(fetchSourceRunTrace).mockImplementation((runId) => Promise.resolve(traceFor(runId)));
    vi.mocked(fetchCardPreviewDetail).mockImplementation(() => lateDetail.promise);
    renderModule();
    await user.click(await screen.findByRole("tab", { name: "调研任务" }));
    await user.click(await screen.findByRole("button", { name: "查看调研 调研 A" }));
    await user.click(await screen.findByRole("button", { name: "预览信息卡" }));
    const cardSignal = vi.mocked(fetchCardPreviewDetail).mock.calls[0][1]!;
    await user.click(screen.getByRole("button", { name: "关闭对话框" }));
    await user.click(screen.getByRole("button", { name: "查看调研 调研 B" }));
    expect(await screen.findByRole("dialog", { name: "调研详情" })).toHaveTextContent("调研 B");
    expect(cardSignal.aborted).toBe(true);
    await act(async () => {
      lateDetail.resolve({ ...researchCardDetail, card: { ...researchCardDetail.card, card_id: "card-a", title: "迟到的卡片 A" } });
      await lateDetail.promise;
    });
    expect(screen.queryByRole("heading", { name: "迟到的卡片 A" })).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "调研详情" })).toHaveTextContent("调研 B");
  });

  it("cancels a previous research page request before another run can receive its items", async () => {
    const user = userEvent.setup();
    const latePage = deferred<typeof traceFixture>();
    const runs = [
      { ...page.items[0], source_run_id: "run-a", source_name: "调研 A", source_type: "keyword", task_view: "research" as const, trace_available: true },
      { ...page.items[0], source_run_id: "run-b", source_name: "调研 B", source_type: "keyword", task_view: "research" as const, trace_available: true },
    ];
    const firstA = {
      ...traceFixture,
      run: { ...traceFixture.run, source_run_id: "run-a", source_name: "调研 A" },
      items: [{ ...traceFixture.items[0], finalize: { ...traceFixture.items[0].finalize, status: "success" } }],
      item_page: {
        items: [{ ...traceFixture.items[0], finalize: { ...traceFixture.items[0].finalize, status: "success" } }],
        item_total: 2, truncated: true, next_cursor: "next-a",
      },
    };
    const traceB = {
      ...traceFixture,
      run: { ...traceFixture.run, source_run_id: "run-b", source_name: "调研 B" },
      items: [{ ...traceFixture.items[1], title: "只属于 B", finalize: { ...traceFixture.items[1].finalize, status: "success" } }],
      item_page: {
        items: [{ ...traceFixture.items[1], title: "只属于 B", finalize: { ...traceFixture.items[1].finalize, status: "success" } }],
        item_total: 1, truncated: false, next_cursor: null,
      },
    };
    vi.mocked(fetchSourceRuns).mockResolvedValue({ ...page, items: runs, total: 2, next_offset: null });
    vi.mocked(fetchSourceRunTrace).mockImplementation((runId, _signal, cursor) => {
      if (runId === "run-a" && cursor) return latePage.promise;
      return Promise.resolve(runId === "run-a" ? firstA : traceB);
    });
    renderModule();
    await user.click(await screen.findByRole("tab", { name: "调研任务" }));
    await user.click(await screen.findByRole("button", { name: "查看调研 调研 A" }));
    await user.click(await screen.findByRole("button", { name: "加载更多信息卡（1 / 2）" }));
    const pageSignal = vi.mocked(fetchSourceRunTrace).mock.calls.find((call) => call[2] === "next-a")?.[1]!;
    await user.click(screen.getByRole("button", { name: "关闭对话框" }));
    await user.click(screen.getByRole("button", { name: "查看调研 调研 B" }));
    expect((await screen.findAllByText("只属于 B")).length).toBeGreaterThan(0);
    expect(pageSignal.aborted).toBe(true);
    await act(async () => {
      latePage.resolve({
        ...firstA,
        items: [{ ...traceFixture.items[2], title: "迟到的 A 条目" }],
        item_page: {
          items: [{ ...traceFixture.items[2], title: "迟到的 A 条目" }],
          item_total: 2, truncated: false, next_cursor: null,
        },
      });
      await latePage.promise;
    });
    expect(screen.queryByText("迟到的 A 条目")).not.toBeInTheDocument();
    expect(screen.getAllByText("只属于 B").length).toBeGreaterThan(0);
  });

  it("shows the scheduled time for a future run before it has started", async () => {
    const scheduledAt = "2026-07-18T08:00:00Z";
    vi.mocked(fetchSourceRuns).mockResolvedValueOnce({
      ...page,
      items: [{
        ...page.items[0], status: "pending", execution_bucket: "future", scheduled_at: scheduledAt, finished_at: null,
        started_at: null, duration_ms: null,
      }],
    });
    renderModule();

    const row = await screen.findByRole("row", { name: /小红书 · 产品观察/ });
    expect(screen.getByRole("columnheader", { name: "计划 / 开始 / 耗时" })).toBeInTheDocument();
    expect(row).toHaveTextContent(dateTime(scheduledAt));
    expect(row).toHaveTextContent("尚未开始");
  });

  it("copies the run ID with toast and keeps the dialog open", async () => {
    const user = userEvent.setup();
    const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    renderModule();
    await user.click(await screen.findByRole("button", { name: "追踪 小红书 · 产品观察" }));
    await screen.findByRole("dialog", { name: "来源运行追踪" });
    await user.click(screen.getByRole("button", { name: "复制运行 ID" }));
    expect(writeText).toHaveBeenCalledWith("run-xhs-001");
    expect(screen.getByRole("status")).toHaveTextContent("运行 ID 已复制");
    expect(screen.getByRole("dialog", { name: "来源运行追踪" })).toBeInTheDocument();
  });

  it("retries only the confirmed failed task and refreshes only the open trace", async () => {
    const user = userEvent.setup();
    renderModule();
    await user.click(await screen.findByRole("button", { name: "追踪 小红书 · 产品观察" }));
    const tree = await screen.findByRole("region", { name: "笔记 1 抓取树" });
    await user.click(within(tree).getByRole("button", { name: "重试任务 task-video-1" }));
    await user.click(within(screen.getByRole("alertdialog", { name: "确认重试失败分支" })).getByRole("button", { name: "确认创建新尝试" }));
    await waitFor(() => expect(retrySourceRunTask).toHaveBeenCalledWith("task-video-1"));
    await waitFor(() => expect(fetchSourceRunTrace).toHaveBeenCalledTimes(2));
    expect(fetchSourceRuns).toHaveBeenCalledTimes(1);
  });

  it("loads the next persisted trace page and appends items without closing the dialog", async () => {
    const user = userEvent.setup();
    const firstPage = { ...traceFixture, item_page: { ...traceFixture.item_page, item_total: 4, truncated: true, next_cursor: "cursor-3" } };
    const fourth = { ...traceFixture.items[2], raw_item_id: "raw-4", card_id: "card-4", title: "笔记 4", content_enrich: { ...traceFixture.items[2].content_enrich, task_id: "task-enrich-4" }, finalize: { ...traceFixture.items[2].finalize, task_id: "task-finalize-4" } };
    const nextPage = { ...traceFixture, items: [fourth], item_page: { items: [fourth], item_total: 4, truncated: false, next_cursor: null } };
    vi.mocked(fetchSourceRunTrace).mockResolvedValueOnce(firstPage).mockResolvedValueOnce(nextPage);
    renderModule();
    await user.click(await screen.findByRole("button", { name: "追踪 小红书 · 产品观察" }));
    await user.click(await screen.findByRole("button", { name: "加载更多条目（3 / 4）" }));
    await waitFor(() => expect(fetchSourceRunTrace).toHaveBeenLastCalledWith("run-xhs-001", expect.anything(), "cursor-3"));
    expect(screen.getByRole("button", { name: "展开笔记 4 抓取树" })).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "来源运行追踪" })).toBeInTheDocument();
  });
});
