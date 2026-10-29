import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TokenAuthenticationModule } from "../TokenAuthenticationModule";
import * as tokenApi from "../../../../lib/api/tokenAuth";
import type {
  RequestLogDetail, RequestLogPage, TokenAdminRecord, TokenCreateResult, TokenOverview,
} from "../../../../lib/types/tokenAuth";

const toastMock = vi.hoisted(() => ({ pushToast: vi.fn(), dismiss: vi.fn() }));

vi.mock("../../../ui/Toast", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../ui/Toast")>();
  return { ...actual, useToast: () => toastMock };
});

vi.mock("../../../../lib/api/tokenAuth", async () => {
  const actual = await vi.importActual<typeof import("../../../../lib/api/tokenAuth")>("../../../../lib/api/tokenAuth");
  return {
    ...actual,
    fetchTokenOverview: vi.fn(),
    fetchAdminTokens: vi.fn(),
    createAdminToken: vi.fn(),
    updateAdminToken: vi.fn(),
    revokeAdminToken: vi.fn(),
    rotateAdminToken: vi.fn(),
    testAdminTokenWebhook: vi.fn(),
    replayAdminTokenWebhooks: vi.fn(),
    fetchRequestLogs: vi.fn(),
    fetchRequestLogDetail: vi.fn(),
  };
});

const overview: TokenOverview = {
  interface_path: "/api/plugin/v1",
  interface_version: "v1",
  service_status: "ready",
  capabilities: ["cards:read", "assets:read", "captures:write"],
};

const tokens: TokenAdminRecord[] = [
  {
    token_id: "token-1", name: "Obsidian 主库", token_prefix: "ios_abcd1234…",
    scopes: ["cards:read", "assets:read"], created_at: "2026-07-16T08:00:00Z",
    last_used_at: "2026-07-16T09:00:00Z", revoked_at: null, replaced_by_token_id: null, status: "active",
    webhook_enabled: false, webhook_url: null, webhook_config_generation: 1, webhook_delivery_status: "inactive",
    webhook_consecutive_failures: 0, webhook_last_attempt_at: null,
    webhook_last_success_at: null, webhook_last_failure_at: null,
    webhook_last_error: null, webhook_circuit_opened_at: null,
  },
];

const createResult: TokenCreateResult = {
  token: { ...tokens[0], token_id: "token-new", name: "Obsidian 新库", token_prefix: "ios_new12345…" },
  plaintext_token: "ios_super_secret_plaintext_once",
};

const requestPage: RequestLogPage = {
  summary: { request_count: 4, success_2xx_count: 2, client_error_4xx_count: 2, median_duration_ms: 18 },
  items: [
    { request_id: "req-200", created_at: "2026-07-16T09:00:00Z", method: "GET", path: "/api/plugin/v1/cards", status_code: 200, result_code: "ok", result_summary: "返回 5 张卡片", token_prefix: "ios_abcd…", client_label: "Obsidian", duration_ms: 12 },
    { request_id: "req-206", created_at: "2026-07-16T09:01:00Z", method: "GET", path: "/api/plugin/v1/assets/a1", status_code: 206, result_code: "partial_content", result_summary: "返回附件范围", token_prefix: "ios_abcd…", client_label: "Obsidian", duration_ms: 18 },
    { request_id: "req-401", created_at: "2026-07-16T09:02:00Z", method: "GET", path: "/api/plugin/v1/cards", status_code: 401, result_code: "token_revoked", result_summary: "Token 已撤销", token_prefix: "ios_old…", client_label: "Obsidian", duration_ms: 8 },
    { request_id: "req-404", created_at: "2026-07-16T09:03:00Z", method: "GET", path: "/api/plugin/v1/cards/missing", status_code: 404, result_code: "card_not_found", result_summary: "卡片不存在", token_prefix: null, client_label: "Unknown", duration_ms: 22 },
  ],
  total: 4,
  next_offset: null,
};

const requestDetail: RequestLogDetail = {
  request_id: "req-206",
  created_at: "2026-07-16T09:01:00Z",
  method: "GET",
  path: "/api/plugin/v1/assets/a1",
  client_label: "Obsidian",
  token_prefix: "ios_abcd…",
  status_code: 206,
  duration_ms: 18,
  result_code: "partial_content",
  result_summary: "返回附件范围",
  response_meta: { response_bytes: 1024, range: "bytes 0-1023/4096" },
};

const overviewMock = vi.mocked(tokenApi.fetchTokenOverview);
const tokensMock = vi.mocked(tokenApi.fetchAdminTokens);
const createMock = vi.mocked(tokenApi.createAdminToken);
const updateMock = vi.mocked(tokenApi.updateAdminToken);
const revokeMock = vi.mocked(tokenApi.revokeAdminToken);
const rotateMock = vi.mocked(tokenApi.rotateAdminToken);
const webhookTestMock = vi.mocked(tokenApi.testAdminTokenWebhook);
const webhookReplayMock = vi.mocked(tokenApi.replayAdminTokenWebhooks);
const logsMock = vi.mocked(tokenApi.fetchRequestLogs);
const detailMock = vi.mocked(tokenApi.fetchRequestLogDetail);
const writeText = vi.fn(async () => undefined);

describe("TokenAuthenticationModule", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/?variant=A&menu=tokens&tab=auth");
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    overviewMock.mockResolvedValue(overview);
    tokensMock.mockResolvedValue(tokens);
    createMock.mockResolvedValue(createResult);
    updateMock.mockResolvedValue(tokens[0]);
    revokeMock.mockResolvedValue(tokens[0]);
    rotateMock.mockResolvedValue({ ...createResult, token: { ...createResult.token, token_id: "token-rotated" } });
    webhookTestMock.mockResolvedValue({ ok: true, status_code: 204, token: tokens[0] });
    webhookReplayMock.mockResolvedValue({ ok: true, replayed_count: 3, task_ids: ["a", "b", "c"] });
    logsMock.mockResolvedValue(requestPage);
    detailMock.mockResolvedValue(requestDetail);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("uses segmented tabs, persists the active tab in the URL, and restores it on mount", async () => {
    const user = userEvent.setup();
    const { unmount } = render(<TokenAuthenticationModule />);
    const authTab = screen.getByRole("tab", { name: "认证管理" });
    expect(authTab).toHaveAttribute("aria-selected", "true");
    expect(authTab).toHaveAttribute("aria-controls", "collector-token-panel-auth");
    await user.click(screen.getByRole("tab", { name: "请求日志" }));
    expect(window.location.search).toBe("?variant=A&menu=tokens&tab=requests");
    expect(await screen.findByText("最近 24 小时请求数")).toBeInTheDocument();
    unmount();

    render(<TokenAuthenticationModule />);
    expect(screen.getByRole("tab", { name: "请求日志" })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByText("req-200")).toBeInTheDocument();
  });

  it("builds the interface address from the current origin and copying keeps the auth tab open", async () => {
    const user = userEvent.setup();
    const clipboardWrite = vi.spyOn(navigator.clipboard, "writeText");
    render(<TokenAuthenticationModule />);
    const address = `${window.location.origin}/api/plugin/v1`;
    expect(await screen.findByText(address)).toBeInTheDocument();
    expect(screen.getByText(/版本 v1/)).toBeInTheDocument();
    expect(screen.getByText("服务正常")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "复制 Interface 地址" }));
    expect(clipboardWrite).toHaveBeenCalledWith(address);
    expect(screen.getByRole("tab", { name: "认证管理" })).toHaveAttribute("aria-selected", "true");
  });

  it("creates the default read scopes, shows plaintext once, confirms before clearing, and copy does not close", async () => {
    const user = userEvent.setup();
    const clipboardWrite = vi.spyOn(navigator.clipboard, "writeText");
    render(<TokenAuthenticationModule />);
    await screen.findByText("Obsidian 主库");
    await user.click(screen.getByRole("button", { name: "创建 Token" }));
    const createDialog = screen.getByRole("dialog", { name: "创建 Token" });
    await user.type(within(createDialog).getByLabelText("Token 名称"), "Obsidian 新库");
    expect(within(createDialog).getByLabelText("cards:read")).toBeChecked();
    expect(within(createDialog).getByLabelText("assets:read")).toBeChecked();
    await user.click(within(createDialog).getByRole("button", { name: "创建 Token" }));
    await waitFor(() => expect(createMock).toHaveBeenCalledWith({ name: "Obsidian 新库", scopes: ["cards:read", "assets:read"], webhook_enabled: false, webhook_url: null }));

    const createdDialog = screen.getByRole("dialog", { name: "Token 明文仅显示一次" });
    expect(within(createdDialog).getByText("ios_super_secret_plaintext_once")).toBeInTheDocument();
    await user.click(within(createdDialog).getByRole("button", { name: "复制 Token" }));
    expect(clipboardWrite).toHaveBeenCalledWith("ios_super_secret_plaintext_once");
    expect(screen.getByRole("dialog", { name: "Token 明文仅显示一次" })).toBeInTheDocument();
    await user.click(within(createdDialog).getByRole("button", { name: "关闭" }));
    expect(within(createdDialog).getByText("请确认已安全保存明文 Token")).toBeInTheDocument();
    await user.click(within(createdDialog).getByRole("button", { name: "确认关闭并清空" }));
    expect(screen.queryByText("ios_super_secret_plaintext_once")).not.toBeInTheDocument();
  });

  it("can create an MCP evaluation token", async () => {
    const user = userEvent.setup();
    render(<TokenAuthenticationModule />);
    await screen.findByText("Obsidian 主库");
    await user.click(screen.getByRole("button", { name: "创建 Token" }));
    const dialog = screen.getByRole("dialog", { name: "创建 Token" });
    await user.type(within(dialog).getByLabelText("Token 名称"), "InfoOS MCP");
    await user.click(within(dialog).getByLabelText("cards:read"));
    await user.click(within(dialog).getByLabelText("assets:read"));
    await user.click(within(dialog).getByLabelText("intelligence:read"));
    await user.click(within(dialog).getByLabelText("evaluation:write"));
    await user.click(within(dialog).getByRole("button", { name: "创建 Token" }));
    await waitFor(() => expect(createMock).toHaveBeenCalledWith({
      name: "InfoOS MCP", scopes: ["intelligence:read", "evaluation:write"], webhook_enabled: false, webhook_url: null,
    }));
  });

  it("can add collect:write to an existing token", async () => {
    const user = userEvent.setup();
    render(<TokenAuthenticationModule />);
    await screen.findByText("Obsidian 主库");
    await user.click(screen.getByRole("button", { name: "编辑 Obsidian 主库" }));
    const dialog = screen.getByRole("dialog", { name: "编辑 Token" });
    expect(within(dialog).getByText("通过 MCP 发起调研、提交链接捕获")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("collect:write")).not.toBeChecked();
    await user.click(within(dialog).getByLabelText("collect:write"));
    await user.click(within(dialog).getByRole("button", { name: "保存 Token 配置" }));
    await waitFor(() => expect(updateMock).toHaveBeenCalledWith("token-1", {
      name: "Obsidian 主库", scopes: ["cards:read", "assets:read", "collect:write"],
      webhook_enabled: false, webhook_url: null,
    }));
  });

  it("creates and edits an optional webhook without exposing a token plaintext", async () => {
    const user = userEvent.setup();
    render(<TokenAuthenticationModule />);
    await screen.findByText("Obsidian 主库");
    expect(screen.getByText("未启用")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "编辑 Obsidian 主库" }));
    const dialog = screen.getByRole("dialog", { name: "编辑 Token" });
    await user.click(within(dialog).getByLabelText("启用 Webhook"));
    await user.type(within(dialog).getByLabelText("Webhook URL"), "http://127.0.0.1:27124/infoos/webhook");
    await user.click(within(dialog).getByRole("button", { name: "保存 Token 配置" }));
    await waitFor(() => expect(updateMock).toHaveBeenCalledWith("token-1", {
      name: "Obsidian 主库", scopes: ["cards:read", "assets:read"], webhook_enabled: true,
      webhook_url: "http://127.0.0.1:27124/infoos/webhook",
    }));
    expect(screen.queryByRole("dialog", { name: "Token 明文仅显示一次" })).not.toBeInTheDocument();
  });

  it("creates a capture-only token from the browser preset", async () => {
    const user = userEvent.setup();
    render(<TokenAuthenticationModule />);
    await screen.findByText("Obsidian 主库");
    await user.click(screen.getByRole("button", { name: "创建 Token" }));
    const dialog = screen.getByRole("dialog", { name: "创建 Token" });
    await user.click(within(dialog).getByRole("button", { name: "使用浏览器捕获预设" }));
    expect(within(dialog).getByLabelText("captures:write")).toBeChecked();
    expect(within(dialog).getByLabelText("cards:read")).not.toBeChecked();
    expect(within(dialog).getByLabelText("assets:read")).not.toBeChecked();
    expect(within(dialog).getByLabelText("启用 Webhook")).toBeDisabled();
    await user.click(within(dialog).getByRole("button", { name: "创建 Token" }));
    await waitFor(() => expect(createMock).toHaveBeenCalledWith({
      name: "Save to InfoOS",
      scopes: ["captures:write"],
      webhook_enabled: false,
      webhook_url: null,
    }));
  });

  it("sends the optional historical-card setting when enabled", async () => {
    const user = userEvent.setup();
    render(<TokenAuthenticationModule />);
    await screen.findByText("Obsidian 主库");
    await user.click(screen.getByRole("button", { name: "编辑 Obsidian 主库" }));
    const dialog = screen.getByRole("dialog", { name: "编辑 Token" });
    await user.click(within(dialog).getByLabelText("启用 Webhook"));
    await user.type(within(dialog).getByLabelText("Webhook URL"), "http://127.0.0.1:27124/hook");
    await user.click(within(dialog).getByLabelText("包含历史卡片"));
    await user.click(within(dialog).getByRole("button", { name: "保存 Token 配置" }));
    await waitFor(() => expect(updateMock).toHaveBeenCalledWith("token-1", expect.objectContaining({
      webhook_include_backfill: true,
    })));
  });

  it("requires confirmation for revocation and rotation, with rotation returning one new plaintext", async () => {
    const user = userEvent.setup();
    render(<TokenAuthenticationModule />);
    await screen.findByText("Obsidian 主库");

    await user.click(screen.getByRole("button", { name: "撤销 Obsidian 主库" }));
    expect(revokeMock).not.toHaveBeenCalled();
    const revokeDialog = screen.getByRole("dialog", { name: "撤销 Token" });
    await user.click(within(revokeDialog).getByRole("button", { name: "确认撤销" }));
    await waitFor(() => expect(revokeMock).toHaveBeenCalledWith("token-1"));

    await user.click(screen.getByRole("button", { name: "轮换 Obsidian 主库" }));
    const rotateDialog = screen.getByRole("dialog", { name: "轮换 Token" });
    await user.click(within(rotateDialog).getByRole("button", { name: "确认轮换" }));
    await waitFor(() => expect(rotateMock).toHaveBeenCalledWith("token-1"));
    expect(screen.getByRole("dialog", { name: "Token 明文仅显示一次" })).toHaveTextContent("ios_super_secret_plaintext_once");
  });

  it("shows a paused webhook, requires a successful test before replay, and reports the replay count", async () => {
    const user = userEvent.setup();
    tokensMock.mockResolvedValueOnce([{
      ...tokens[0], webhook_enabled: true, webhook_url: "http://127.0.0.1:27124/hook",
      webhook_delivery_status: "paused", webhook_consecutive_failures: 3,
      webhook_last_attempt_at: "2026-07-20T08:00:00Z",
      webhook_last_failure_at: "2026-07-20T08:00:00Z",
      webhook_last_error: "connection refused",
      webhook_circuit_opened_at: "2026-07-20T08:00:00Z",
    }]);
    render(<TokenAuthenticationModule />);
    expect(await screen.findByText("已暂停")).toBeInTheDocument();
    expect(screen.getByText("连续失败 3 次")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重放 Obsidian 主库 Webhook" })).toBeDisabled();

    tokensMock.mockResolvedValue(tokens.map((token) => ({
      ...token, webhook_enabled: true, webhook_url: "http://127.0.0.1:27124/hook",
      webhook_delivery_status: "active",
    })));
    await user.click(screen.getByRole("button", { name: "测试 Obsidian 主库 Webhook" }));
    await waitFor(() => expect(webhookTestMock).toHaveBeenCalledWith("token-1"));
    await waitFor(() => expect(screen.getByRole("button", { name: "重放 Obsidian 主库 Webhook" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "重放 Obsidian 主库 Webhook" }));
    await waitFor(() => expect(webhookReplayMock).toHaveBeenCalledWith("token-1"));
    expect(toastMock.pushToast).toHaveBeenCalledWith(expect.objectContaining({ message: "已重新排队 3 条失败通知" }));
  });

  it("keeps a newly generated webhook pending until the signed test succeeds", async () => {
    tokensMock.mockResolvedValueOnce([{
      ...tokens[0], webhook_enabled: true, webhook_url: "http://127.0.0.1:27124/hook",
      webhook_delivery_status: "pending_verification",
    }]);

    render(<TokenAuthenticationModule />);

    expect(await screen.findByText("待验证")).toBeInTheDocument();
    expect(screen.getByText("配置接收端后点击测试")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "测试 Obsidian 主库 Webhook" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "重放 Obsidian 主库 Webhook" })).toBeDisabled();
  });

  it("shows filtered request metrics and the fixed log column order including 206, 401, and 404", async () => {
    const user = userEvent.setup();
    window.history.replaceState({}, "", "/?menu=tokens&tab=requests");
    render(<TokenAuthenticationModule />);
    expect((await screen.findAllByText("18 ms")).length).toBeGreaterThan(0);
    const table = screen.getByRole("table", { name: "Token 请求日志" });
    expect(within(table).getByText("206")).toBeInTheDocument();
    expect(within(table).getByText("401")).toBeInTheDocument();
    expect(within(table).getByText("404")).toBeInTheDocument();
    expect(within(table).getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual([
      "时间", "状态码", "返回结果", "接口", "Token / 客户端", "耗时", "详情",
    ]);
    await user.type(screen.getByRole("searchbox", { name: "搜索请求日志" }), "req-401");
    fireEvent.submit(screen.getByRole("search", { name: "请求日志筛选" }));
    await user.selectOptions(screen.getByLabelText("HTTP 状态"), "4xx");
    await user.selectOptions(screen.getByLabelText("时间范围"), "7d");
    await waitFor(() => expect(logsMock).toHaveBeenLastCalledWith(
      expect.objectContaining({ query: "req-401", status: "4xx", time_range: "7d" }),
      expect.any(AbortSignal),
    ));
  });

  it("shows every request detail field, formats response_meta, and copying Request ID keeps the dialog open", async () => {
    const user = userEvent.setup();
    const clipboardWrite = vi.spyOn(navigator.clipboard, "writeText");
    window.history.replaceState({}, "", "/?menu=tokens&tab=requests");
    render(<TokenAuthenticationModule />);
    await screen.findByText("206");
    await user.click(screen.getByRole("button", { name: "详情 req-206" }));
    await waitFor(() => expect(detailMock).toHaveBeenCalledWith("req-206", expect.any(AbortSignal)));
    const dialog = await screen.findByRole("dialog", { name: "请求详情" });
    for (const text of ["GET", "/api/plugin/v1/assets/a1", "Obsidian", "ios_abcd…", "206", "18 ms", "req-206", "返回附件范围"]) {
      expect(within(dialog).getByText(text)).toBeInTheDocument();
    }
    expect(within(dialog).getByText(/bytes 0-1023\/4096/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "复制 Request ID" }));
    expect(clipboardWrite).toHaveBeenCalledWith("req-206");
    expect(screen.getByRole("dialog", { name: "请求详情" })).toBeInTheDocument();
  });

  it("exposes loading and error states without sync, Vault, or Markdown concepts", async () => {
    overviewMock.mockRejectedValueOnce(new Error("overview unavailable"));
    render(<TokenAuthenticationModule />);
    expect(screen.getByText("正在读取 Token 配置…")).toBeInTheDocument();
    expect(await screen.findByRole("alert")).toHaveTextContent("overview unavailable");
    expect(screen.queryByText(/同步进度|待同步|Vault|Markdown/)).not.toBeInTheDocument();
  });
});
