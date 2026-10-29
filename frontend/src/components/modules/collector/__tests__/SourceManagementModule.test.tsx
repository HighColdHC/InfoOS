import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConfirmProvider } from "../../../ui/hooks/useConfirm";
import { SourceManagementModule } from "../SourceManagementModule";
import * as sourceApi from "../../../../lib/api/sourceManagement";
import type {
  SourceManagementDetail,
  SourceManagementPage,
  SourceManagementResult,
  SourceRunSubmissionResult,
} from "../../../../lib/types/sourceManagement";

const toastMock = vi.hoisted(() => ({ pushToast: vi.fn(), dismiss: vi.fn() }));

vi.mock("../../../ui/Toast", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../ui/Toast")>();
  return { ...actual, useToast: () => toastMock };
});

vi.mock("../../../../lib/api/sourceManagement", async () => {
  const actual = await vi.importActual<typeof import("../../../../lib/api/sourceManagement")>(
    "../../../../lib/api/sourceManagement",
  );
  return {
    ...actual,
    fetchSourcePage: vi.fn(),
    fetchSourceDetail: vi.fn(),
    fetchSourcePlatformCapabilities: vi.fn(),
    createManagedSource: vi.fn(),
    updateManagedSource: vi.fn(),
    setManagedSourceEnabled: vi.fn(),
    submitManagedSourceRun: vi.fn(),
  };
});

const page: SourceManagementPage = {
  summary: { total_sources: 8, enabled_sources: 6, needs_attention: 2, reliability_7d: 0.936 },
  items: [
    {
      source_id: "source-x",
      display_name: "产品动态",
      platform: "x",
      source_type: "account",
      identifier: "openai",
      health_status: "healthy",
      health_label: "健康",
      status_explanation: "最近采集成功",
      cadence_hours: 2,
      max_items_per_run: 20,
      auto_enrich_media: true,
      enabled: true,
      latest_run_at: "2026-07-16T08:00:00Z",
      latest_run_status: "success",
      readiness_status: "ready",
      login_status: "verified",
      connector_id: "opencli_x",
      generated_card_count: 0,
      raw_metadata: { auto_enrich_media: true, feed_provider: "generic", feed_content_mode: "feed_preferred" },
    },
    {
      source_id: "source-xhs",
      display_name: "小红书观察",
      platform: "xiaohongshu",
      source_type: "account",
      identifier: "xhs-user",
      health_status: "needs_attention",
      health_label: "需处理",
      status_explanation: "登录已失效",
      cadence_hours: 6,
      max_items_per_run: 10,
      auto_enrich_media: true,
      enabled: true,
      latest_run_at: "2026-07-15T08:00:00Z",
      latest_run_status: "blocked",
      readiness_status: "login_required",
      login_status: "needs_login",
      connector_id: "opencli_xhs",
      generated_card_count: 0,
      raw_metadata: { auto_enrich_media: true, feed_provider: "generic", feed_content_mode: "feed_preferred" },
    },
  ],
  total: 2,
  limit: 25,
  offset: 0,
};

const detail: SourceManagementDetail = {
  source: {
    ...page.items[0],
    notes: "官方产品信息",
    next_run_at: "2026-07-16T10:00:00Z",
    generated_card_count: 38,
  },
  status_explanation: "最近 7 天 14 次运行，全部成功。",
  current_config_version: "cfg:abc123",
  config_effective_at: "2026-07-16T07:30:00Z",
  recent_run_stats: {
    total: 14,
    success: 14,
    failed: 0,
    cards_created: 38,
    last_run_at: "2026-07-16T08:00:00Z",
    last_status: "success",
  },
};

const saveResult: SourceManagementResult = {
  source: page.items[0],
  source_id: "source-x",
  source_run_id: "task-fetch-1",
  task_id: "task-fetch-1",
  initial_fetch: true,
  reused: false,
};

const runResult: SourceRunSubmissionResult = {
  source_run_id: null,
  task_id: "task-run-2",
  batch_id: "batch-run-2",
};

const listMock = vi.mocked(sourceApi.fetchSourcePage);
const detailMock = vi.mocked(sourceApi.fetchSourceDetail);
const capabilityMock = vi.mocked(sourceApi.fetchSourcePlatformCapabilities);
const createMock = vi.mocked(sourceApi.createManagedSource);
const updateMock = vi.mocked(sourceApi.updateManagedSource);
const enabledMock = vi.mocked(sourceApi.setManagedSourceEnabled);
const runMock = vi.mocked(sourceApi.submitManagedSourceRun);

function renderModule() {
  return render(<ConfirmProvider><SourceManagementModule /></ConfirmProvider>);
}

describe("SourceManagementModule", () => {
  beforeEach(() => {
    listMock.mockResolvedValue(page);
    detailMock.mockResolvedValue(detail);
    capabilityMock.mockResolvedValue([
      {
        platform: "x",
        label: "X",
        source_types: [
          { source_type: "account", label: "账号" },
          {
            source_type: "home_feed",
            label: "首页 Feed",
            default_cadence_hours: 1,
            default_max_items: 20,
            identifier_mode: "select" as const,
            identifier_options: [
              { value: "for-you", label: "为你推荐" },
              { value: "following", label: "正在关注" },
            ],
          },
        ],
        cadence_options: [0.25, 0.5, 1, 5, 6, 24],
        max_items_limit: 100,
        default_max_items: 20,
      },
      {
        platform: "youtube",
        label: "YouTube",
        source_types: [
          { source_type: "channel", label: "频道" },
          {
            source_type: "home_feed",
            label: "首页 Feed",
            default_cadence_hours: 6,
            default_max_items: 20,
            identifier_mode: "select" as const,
            identifier_options: [{ value: "recommended", label: "推荐" }],
          },
        ],
        cadence_options: [0.5, 1, 5, 6, 24],
        max_items_limit: 100,
        default_max_items: 20,
      },
      {
        platform: "reddit",
        label: "Reddit",
        source_types: [
          { source_type: "subreddit", label: "社区" },
          {
            source_type: "home_feed",
            label: "首页 Feed",
            default_cadence_hours: 1,
            default_max_items: 20,
            identifier_mode: "select" as const,
            identifier_options: [{ value: "best", label: "最佳" }],
          },
        ],
        cadence_options: [0.5, 1, 5, 24],
        max_items_limit: 100,
        default_max_items: 20,
      },
      {
        platform: "xiaohongshu",
        label: "小红书",
        source_types: [
          { source_type: "account", label: "账号" },
          {
            source_type: "home_feed",
            label: "首页 Feed",
            default_cadence_hours: 5,
            default_max_items: 20,
            identifier_mode: "select" as const,
            identifier_options: [{ value: "recommended", label: "推荐" }],
          },
        ],
        cadence_options: [5, 24],
        max_items_limit: 50,
        default_max_items: 20,
      },
      {
        platform: "rss",
        label: "RSS",
        source_types: [{ source_type: "feed", label: "订阅源" }],
        cadence_options: [6, 12, 24],
        max_items_limit: 100,
        default_max_items: 20,
      },
      {
        platform: "producthunt",
        label: "Product Hunt",
        source_types: [
          { source_type: "topics", label: "主题", default_cadence_hours: 24, default_max_items: 30 },
          { source_type: "collections", label: "合集", default_cadence_hours: 12, default_max_items: 8 },
        ],
        cadence_options: [6, 12, 24],
        max_items_limit: 100,
        default_max_items: 20,
      },
      {
        platform: "zsxq",
        label: "生财有术",
        source_types: [
          { source_type: "topics", label: "全部帖子", default_cadence_hours: 5, default_max_items: 20, identifier_mode: "select" as const, identifier_options: [{ value: "all", label: "全部" }, { value: "ai", label: "AI" }] },
          { source_type: "keyword", label: "关键词", default_cadence_hours: 5, default_max_items: 10, max_items_limit: 100, identifier_hint: "站内搜索关键词" },
        ],
        cadence_options: [5, 24],
        max_items_limit: 100,
        default_max_items: 20,
      },
    ].map((platform) => ({
      ...platform,
      source_types: platform.source_types.map((sourceType) => ({
        default_cadence_hours: platform.cadence_options[0],
        default_max_items: platform.default_max_items,
        identifier_mode: "text" as const,
        identifier_options: [],
        identifier_hint: null,
        requires_login: "no",
        login_verifiable: "n/a",
        max_items_limit: platform.max_items_limit,
        ...sourceType,
      })),
    })));
    createMock.mockResolvedValue(saveResult);
    updateMock.mockResolvedValue({ ...saveResult, source_run_id: null, task_id: null, initial_fetch: false });
    enabledMock.mockResolvedValue({ ...saveResult, source_run_id: null, task_id: null, initial_fetch: false });
    runMock.mockResolvedValue(runResult);

  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders server metrics and source rows without knowledge-management or delete actions", async () => {
    renderModule();
    expect(screen.getByText("正在读取信息源…")).toBeInTheDocument();
    expect(await screen.findByText("93.6%")).toBeInTheDocument();
    expect(screen.getByText("产品动态")).toBeInTheDocument();
    expect(screen.getByText("小红书观察")).toBeInTheDocument();
    expect(screen.getByText("最近采集成功")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /删除/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/领域|事件|决策|输出配置/)).not.toBeInTheDocument();
  });

  it("shows the suggested interval and applies it through the existing update API", async () => {
    listMock.mockResolvedValueOnce({
      ...page,
      cadence_suggestions: {
        "source-x": { suggested_cadence_hours: 5, new_items_7d: 8, reason: "近 7 天有效新增 8 条" },
      },
    });
    renderModule();
    expect(await screen.findByText(/建议：每 5 小时/)).toBeInTheDocument();
    expect(updateMock).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "采用建议间隔 产品动态" }));
    await waitFor(() => expect(updateMock).toHaveBeenCalledWith(
      "source-x", expect.objectContaining({ cadence_hours: 5, identifier: "openai" }),
    ));
  });

  it("requests server filtering and pagination while resetting offset on filter changes", async () => {
    const user = userEvent.setup();
    listMock.mockResolvedValueOnce({ ...page, total: 30 });
    renderModule();
    await screen.findByText("产品动态");
    expect(within(screen.getByLabelText("来源平台")).getByRole("option", { name: "RSS" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "下一页" }));
    await waitFor(() => expect(listMock).toHaveBeenLastCalledWith(
      expect.objectContaining({ offset: 25 }),
      expect.any(AbortSignal),
    ));
    await user.selectOptions(screen.getByLabelText("来源平台"), "x");
    await waitFor(() => expect(listMock).toHaveBeenLastCalledWith(
      expect.objectContaining({ platform: "x", offset: 0 }),
      expect.any(AbortSignal),
    ));
    await user.type(screen.getByRole("searchbox", { name: "搜索信息源" }), "产品");
    fireEvent.submit(screen.getByRole("search", { name: "来源筛选" }));
    await waitFor(() => expect(listMock).toHaveBeenLastCalledWith(
      expect.objectContaining({ query: "产品", offset: 0 }),
      expect.any(AbortSignal),
    ));
  });

  it("creates a source with the exact form contract and reports source/run IDs", async () => {
    const user = userEvent.setup();
    renderModule();
    await screen.findByText("产品动态");
    await user.click(screen.getByRole("button", { name: "新增信息源" }));

    const dialog = screen.getByRole("dialog", { name: "新增信息源" });
    await user.selectOptions(within(dialog).getByLabelText("平台"), "x");
    await user.type(within(dialog).getByLabelText("来源名称"), "  新产品源  ");
    await user.type(within(dialog).getByLabelText("来源地址或标识"), "new-product");
    await user.selectOptions(within(dialog).getByLabelText("采集频率"), "6");
    await user.clear(within(dialog).getByLabelText("单次最大条数"));
    await user.type(within(dialog).getByLabelText("单次最大条数"), "15");
    await user.click(within(dialog).getByRole("button", { name: "保存并首次采集" }));

    await waitFor(() => expect(createMock).toHaveBeenCalledWith(expect.objectContaining({
      platform: "x",
      source_type: "account",
      display_name: "新产品源",
      identifier: "new-product",
      cadence_hours: 6,
      max_items_per_run: 15,
      enabled: true,
      raw_metadata: {
        auto_enrich_media: true,
        feed_provider: "generic",
        feed_content_mode: "feed_preferred",
      },
    })));
    expect(toastMock.pushToast).toHaveBeenCalledWith(expect.objectContaining({
      message: expect.stringContaining("source-x"),
    }));
    expect(toastMock.pushToast).toHaveBeenCalledWith(expect.objectContaining({
      message: expect.stringContaining("task-fetch-1"),
    }));
    expect(screen.getByRole("link", { name: "前往采集任务" })).toHaveAttribute("href", "?menu=tasks");
  });

  it("applies Product Hunt type defaults when creating a source", async () => {
    const user = userEvent.setup();
    renderModule();
    await screen.findByText("产品动态");
    await user.click(screen.getByRole("button", { name: "新增信息源" }));
    const dialog = screen.getByRole("dialog", { name: "新增信息源" });

    await user.selectOptions(within(dialog).getByLabelText("平台"), "producthunt");
    expect(within(dialog).getByLabelText("来源类型")).toHaveValue("topics");
    expect(within(dialog).getByLabelText("采集频率")).toHaveValue("24");
    expect(within(dialog).getByLabelText("单次最大条数")).toHaveValue(30);
    await user.selectOptions(within(dialog).getByLabelText("来源类型"), "collections");
    expect(within(dialog).getByLabelText("采集频率")).toHaveValue("12");
    expect(within(dialog).getByLabelText("单次最大条数")).toHaveValue(8);
  });

  it("creates an X home feed from capability-provided modes and defaults", async () => {
    const user = userEvent.setup();
    renderModule();
    await screen.findByText("产品动态");
    await user.click(screen.getByRole("button", { name: "新增信息源" }));
    const dialog = screen.getByRole("dialog", { name: "新增信息源" });

    await user.selectOptions(within(dialog).getByLabelText("平台"), "x");
    await user.selectOptions(within(dialog).getByLabelText("来源类型"), "home_feed");
    expect(within(dialog).getByLabelText("来源地址或标识")).toHaveValue("for-you");
    expect(within(dialog).getByRole("option", { name: "正在关注" })).toHaveValue("following");
    expect(within(dialog).getByLabelText("采集频率")).toHaveValue("1");
    expect(within(dialog).getByLabelText("单次最大条数")).toHaveValue(20);

    await user.selectOptions(within(dialog).getByLabelText("来源地址或标识"), "following");
    await user.selectOptions(within(dialog).getByLabelText("采集频率"), "5");
    await user.type(within(dialog).getByLabelText("来源名称"), "X 正在关注");
    await user.click(within(dialog).getByRole("button", { name: "保存并首次采集" }));

    await waitFor(() => expect(createMock).toHaveBeenCalledWith(expect.objectContaining({
      platform: "x",
      source_type: "home_feed",
      identifier: "following",
      cadence_hours: 5,
      max_items_per_run: 20,
    })));
  });

  it.each([
    ["youtube", "recommended", "6"],
    ["reddit", "best", "1"],
    ["xiaohongshu", "recommended", "5"],
  ])(
    "creates the %s home feed with capability defaults",
    async (platform, identifier, cadence) => {
      const user = userEvent.setup();
      renderModule();
      await screen.findByText("产品动态");
      await user.click(screen.getByRole("button", { name: "新增信息源" }));
      const dialog = screen.getByRole("dialog", { name: "新增信息源" });

      await user.selectOptions(within(dialog).getByLabelText("平台"), platform);
      await user.selectOptions(within(dialog).getByLabelText("来源类型"), "home_feed");
      expect(within(dialog).getByLabelText("来源地址或标识")).toHaveValue(identifier);
      expect(within(dialog).getByLabelText("采集频率")).toHaveValue(cadence);
      expect(within(dialog).getByLabelText("单次最大条数")).toHaveValue(20);

      await user.type(within(dialog).getByLabelText("来源名称"), `${platform} 首页`);
      await user.click(within(dialog).getByRole("button", { name: "保存并首次采集" }));

      await waitFor(() => expect(createMock).toHaveBeenCalledWith(expect.objectContaining({
        platform,
        source_type: "home_feed",
        identifier,
        cadence_hours: Number(cadence),
        max_items_per_run: 20,
      })));
    },
  );

  it("uses ZSXQ topic choices and excludes retired SCYS", async () => {
    const user = userEvent.setup();
    renderModule();
    await screen.findByText("产品动态");
    await user.click(screen.getByRole("button", { name: "新增信息源" }));
    const dialog = screen.getByRole("dialog", { name: "新增信息源" });

    expect(Array.from(within(dialog).getByLabelText("平台").querySelectorAll("option"), (option) => option.value)).not.toContain("scys");
    await user.selectOptions(within(dialog).getByLabelText("平台"), "zsxq");
    expect(within(dialog).getByRole("option", { name: "AI" })).toBeInTheDocument();
    await user.selectOptions(within(dialog).getByLabelText("来源地址或标识"), "ai");
    await user.type(within(dialog).getByLabelText("来源名称"), "生财 AI 精华");
    await user.click(within(dialog).getByRole("button", { name: "保存并首次采集" }));

    await waitFor(() => expect(createMock).toHaveBeenCalledWith(expect.objectContaining({
      platform: "zsxq", source_type: "topics", identifier: "ai", cadence_hours: 5, max_items_per_run: 20,
    })));
  });

  it("submits ZSXQ website-native sort and date filters for keyword research", async () => {
    const user = userEvent.setup();
    renderModule();
    await screen.findByText("产品动态");
    await user.click(screen.getByRole("button", { name: "新增信息源" }));
    const dialog = screen.getByRole("dialog", { name: "新增信息源" });

    await user.selectOptions(within(dialog).getByLabelText("平台"), "zsxq");
    await user.selectOptions(within(dialog).getByLabelText("来源类型"), "keyword");
    expect(within(dialog).getByLabelText("单次最大条数")).toHaveAttribute("max", "100");
    await user.type(within(dialog).getByLabelText("来源名称"), "AI 网站出海调研");
    await user.type(within(dialog).getByLabelText("来源地址或标识"), "AI 网站出海");
    await user.selectOptions(within(dialog).getByLabelText("排序方式"), "newest");
    await user.selectOptions(within(dialog).getByLabelText("文章类型"), "essence");
    fireEvent.change(within(dialog).getByLabelText("补充关键词（可选）"), {
      target: { value: "web出海，AI网站出海\n网站出海" },
    });
    await user.type(within(dialog).getByLabelText("开始日期"), "2024-07-30");
    await user.type(within(dialog).getByLabelText("结束日期"), "2026-07-30");
    await user.clear(within(dialog).getByLabelText("单次最大条数"));
    await user.type(within(dialog).getByLabelText("单次最大条数"), "100");
    await user.click(within(dialog).getByRole("button", { name: "保存并首次采集" }));

    await waitFor(() => expect(createMock).toHaveBeenCalledWith(expect.objectContaining({
      platform: "zsxq",
      source_type: "keyword",
      identifier: "AI 网站出海",
      max_items_per_run: 100,
      raw_metadata: expect.objectContaining({
        zsxq_search: {
          sort: "newest",
          content_type: "essence",
          keywords: ["web出海", "AI网站出海", "网站出海"],
          start_date: "2024-07-30",
          end_date: "2026-07-30",
        },
      }),
    })));
  });

  it("edits mutable fields only and displays version effectiveness and initial-fetch result", async () => {
    const legacyAi = { enabled: true, provider_id: "retired-provider", extra: { retained: true } };
    listMock.mockResolvedValueOnce({ ...page, items: [{ ...page.items[0], raw_metadata: {
      auto_enrich_media: true, feed_provider: "generic", feed_content_mode: "feed_preferred", ai_processing: legacyAi,
    } }, ...page.items.slice(1)] });
    const user = userEvent.setup();
    updateMock.mockResolvedValueOnce({
      ...saveResult,
      source: { ...page.items[0], current_config_version: "cfg:new" },
      source_run_id: "task-refetch-9",
      task_id: "task-refetch-9",
      initial_fetch: true,
    });
    renderModule();
    await screen.findByText("产品动态");
    await user.click(screen.getByRole("button", { name: "编辑 产品动态" }));
    const dialog = screen.getByRole("dialog", { name: "编辑信息源" });

    expect(within(dialog).getByLabelText("平台")).toBeDisabled();
    expect(within(dialog).getByLabelText("来源类型")).toBeDisabled();
    expect(within(dialog).getByLabelText("来源地址或标识")).toBeDisabled();
    expect(within(dialog).getByText(/opencli_x/)).toBeInTheDocument();
    await user.clear(within(dialog).getByLabelText("单次最大条数"));
    await user.type(within(dialog).getByLabelText("单次最大条数"), "25");
    await user.click(within(dialog).getByRole("button", { name: "保存新配置版本" }));

    await waitFor(() => expect(updateMock).toHaveBeenCalled());
    expect(updateMock).toHaveBeenCalledWith("source-x", expect.objectContaining({
      raw_metadata: expect.objectContaining({ ai_processing: legacyAi, feed_provider: "generic", feed_content_mode: "feed_preferred" }),
    }));
    expect(await screen.findByText(/cfg:new/)).toBeInTheDocument();
    expect(screen.getByText(/task-refetch-9/)).toBeInTheDocument();
    expect(screen.getByText(/已提交 Initial Fetch/)).toBeInTheDocument();
  });

  it("requires confirmation for activation and manual runs and exposes busy state", async () => {
    const user = userEvent.setup();
    let resolveEnabled: ((value: SourceManagementResult) => void) | undefined;
    enabledMock.mockReturnValueOnce(new Promise((resolve) => { resolveEnabled = resolve; }));
    renderModule();
    await screen.findByText("产品动态");

    await user.click(screen.getByRole("button", { name: "停用 产品动态" }));
    const enabledDialog = screen.getByRole("dialog", { name: /停用信息源/ });
    await user.click(within(enabledDialog).getByRole("button", { name: "确认停用" }));
    expect(enabledMock).toHaveBeenCalledWith("source-x", false);
    expect(screen.getByRole("button", { name: "停用 产品动态" })).toBeDisabled();
    resolveEnabled?.({ ...saveResult, source_run_id: null, task_id: null, initial_fetch: false });
    await waitFor(() => expect(screen.getByRole("button", { name: "停用 产品动态" })).not.toBeDisabled());

    await user.click(screen.getByRole("button", { name: "运行 产品动态" }));
    const runDialog = screen.getByRole("dialog", { name: "提交手动采集" });
    expect(runMock).not.toHaveBeenCalled();
    await user.click(within(runDialog).getByRole("button", { name: "提交采集任务" }));
    await waitFor(() => expect(runMock).toHaveBeenCalledWith("source-x"));
    expect(toastMock.pushToast).toHaveBeenCalledWith(expect.objectContaining({ kind: "success", message: expect.stringContaining("task-run-2") }));
  });

  it("blocks manual runs when login is invalid and links to the environment page", async () => {
    const user = userEvent.setup();
    renderModule();
    await screen.findByText("小红书观察");
    await user.click(screen.getByRole("button", { name: "运行 小红书观察" }));

    const dialog = screen.getByRole("dialog", { name: "采集环境需要处理" });
    expect(within(dialog).getByText(/小红书/)).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "前往采集环境" })).toHaveAttribute("href", "?menu=environment");
    expect(runMock).not.toHaveBeenCalled();
  });

  it("allows optional-login YouTube sources to run when the platform account is logged out", async () => {
    const user = userEvent.setup();
    const youtubeChannel = {
      ...page.items[0],
      source_id: "source-youtube-channel",
      display_name: "YouTube 公开频道",
      platform: "youtube",
      source_type: "channel",
      identifier: "@openai",
      readiness_status: "ready",
      login_status: "needs_login",
    };
    listMock.mockResolvedValueOnce({
      ...page,
      items: [youtubeChannel],
      total: 1,
    });
    renderModule();
    await screen.findByText("YouTube 公开频道");
    await user.click(screen.getByRole("button", { name: "运行 YouTube 公开频道" }));

    const dialog = screen.getByRole("dialog", { name: "提交手动采集" });
    await user.click(within(dialog).getByRole("button", { name: "提交采集任务" }));

    await waitFor(() => expect(runMock).toHaveBeenCalledWith("source-youtube-channel"));
  });

  it("preserves duplicate-source evidence and opens the existing source for editing", async () => {
    const user = userEvent.setup();
    createMock.mockRejectedValueOnce(new sourceApi.SourceManagementApiError(
      "duplicate_source",
      "Source already exists",
      409,
      "source-x",
    ));
    renderModule();
    await screen.findByText("产品动态");
    await user.click(screen.getByRole("button", { name: "新增信息源" }));
    const dialog = screen.getByRole("dialog", { name: "新增信息源" });
    await user.selectOptions(within(dialog).getByLabelText("平台"), "x");
    await user.type(within(dialog).getByLabelText("来源名称"), "重复来源");
    await user.type(within(dialog).getByLabelText("来源地址或标识"), "openai");
    await user.click(within(dialog).getByRole("button", { name: "保存并首次采集" }));

    const openExisting = await within(dialog).findByRole("button", { name: "打开既有来源 source-x" });
    await user.click(openExisting);
    await waitFor(() => expect(detailMock).toHaveBeenCalledWith("source-x", expect.any(AbortSignal)));
    expect(await screen.findByRole("dialog", { name: "信息源详情" })).toBeInTheDocument();
  });

  it("loads source details with configuration evidence and can route run through one confirmation", async () => {
    const user = userEvent.setup();
    renderModule();
    await screen.findByText("产品动态");
    await user.click(screen.getByRole("button", { name: "详情 产品动态" }));
    await waitFor(() => expect(detailMock).toHaveBeenCalledWith("source-x", expect.any(AbortSignal)));

    const dialog = await screen.findByRole("dialog", { name: "信息源详情" });
    expect(within(dialog).getByText("cfg:abc123")).toBeInTheDocument();
    expect(within(dialog).getByText("健康")).toBeInTheDocument();
    expect(within(dialog).getByText("最近运行状态").parentElement).toHaveTextContent("成功");
    expect(within(dialog).getByText(/最近运行时间/).parentElement).toHaveTextContent("2026年7月16日");
    expect(within(dialog).getAllByText(/2026年7月16日/).length).toBeGreaterThan(0);
    expect(within(dialog).getByText("最近 7 天 14 次运行，全部成功。")).toBeInTheDocument();
    expect(within(dialog).getByText(/成功 14/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "立即采集" }));
    expect(await screen.findByRole("dialog", { name: "提交手动采集" })).toBeInTheDocument();
    expect(runMock).not.toHaveBeenCalled();
  });
});
