import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  checkPlatformAccount,
  fetchCollectionEnvironment,
  launchDedicatedChrome,
  launchPlatformLogin,
} from "../../../../lib/api/collectionEnvironment";
import { fetchAuthRecovery, openAuthRecoveryConsole, openPendingAuthRecoveryConsole, verifyAuthRecovery, verifyPendingAuthRecovery } from "../../../../lib/api/authRecovery";
import { loadBrowserVisibility, saveBrowserVisibility } from "../../../../lib/api";
import type { AuthRecoverySummary, CollectionEnvironmentSnapshot } from "../../../../lib/types/collectionEnvironment";
import type { AuthRecoveryIncident } from "../../../../lib/types/authRecovery";
import { ToastProvider } from "../../../ui/Toast";
import { CollectionEnvironmentModule } from "../CollectionEnvironmentModule";

vi.mock("../collectionEnvironment/NotificationSettings", () => ({ NotificationSettings: () => <div>通知设置</div> }));

vi.mock("../../../../lib/api/collectionEnvironment", () => ({
  fetchCollectionEnvironment: vi.fn(),
  launchDedicatedChrome: vi.fn(),
  checkPlatformAccount: vi.fn(),
  launchPlatformLogin: vi.fn(),
}));

vi.mock("../../../../lib/api/authRecovery", () => ({
  fetchAuthRecovery: vi.fn(),
  openAuthRecoveryConsole: vi.fn(),
  openPendingAuthRecoveryConsole: vi.fn(),
  verifyAuthRecovery: vi.fn(),
  verifyPendingAuthRecovery: vi.fn(),
}));

vi.mock("../../../../lib/api", () => ({
  loadBrowserVisibility: vi.fn(),
  saveBrowserVisibility: vi.fn(),
}));


const componentNames = ["专用 Chrome", "OpenCLI Bridge", "yt-dlp", "gallery-dl", "FFmpeg", "本地 ASR", "内容处理模型", "SQLite"];
const platforms = [
  ["x", "X", "verified", "cookie", "session-x", 2],
  ["youtube", "YouTube", "needs_login", "cookie", "session-yt", 3],
  ["reddit", "Reddit", "public", "public_direct", "无需会话", 0],
  ["xiaohongshu", "小红书", "verified", "dedicated_chrome", "session-xhs", 4],
  ["douyin", "抖音", "unknown", "dedicated_chrome", "session-dy", 1],
  ["scys", "生财有术", "needs_login", "dedicated_chrome", "session-scys", 5],
] as const;

function snapshot(statusOverride?: Partial<Record<string, string>>): CollectionEnvironmentSnapshot {
  return {
    snapshot_at: "2026-07-16T08:00:00Z",
    runtime_circuits: [],
    pending_auth_recoveries: [],
    components: componentNames.map((name, index) => ({ id: index === 0 ? "dedicated_chrome" : `component-${index}`, name, status: index === 6 ? "configured" : "installed", version: index === 0 ? "126.0" : `1.${index}`, is_chrome: index === 0, action: index === 0 ? "launch" : null })),
    accounts: platforms.map(([platform, label, status, browser_capability, session_id, affected_source_count]) => ({
      platform, label, status: statusOverride?.[platform] ?? status, browser_capability, session_id,
      last_checked_at: platform === "douyin" ? null : "2026-07-16T08:30:00Z", affected_source_count,
      action_hint: status === "needs_login" ? "请在专用 Chrome 中登录" : null,
      error: status === "needs_login" ? "会话失效" : null,
    })),
  };
}

function recoveryIncident(overrides: Partial<AuthRecoveryIncident> = {}): AuthRecoveryIncident {
  return {
    incident_id: "incident-1",
    platform: "x",
    platform_label: "X",
    status: "awaiting_user" as const,
    reason_code: "platform_session_not_ready",
    created_at: "2026-07-26T08:00:00Z",
    updated_at: "2026-07-26T08:00:00Z",
    resolved_at: null,
    affected_task_count: 3,
    recovered_task_count: 0,
    last_error_code: "platform_session_not_ready",
    ...overrides,
  };
}

function pendingRecovery(overrides: Partial<AuthRecoverySummary> = {}): AuthRecoverySummary {
  return {
    incident_id: "incident-1",
    platform: "x",
    status: "awaiting_user",
    reason_code: "platform_session_not_ready",
    affected_task_count: 3,
    created_at: "2026-07-27T08:00:00Z",
    updated_at: "2026-07-27T08:00:00Z",
    last_error_code: "platform_session_not_ready",
    open_console_url: "/api/auth-recoveries/incident-1/open-console",
    verify_url: "/api/auth-recoveries/incident-1/verify",
    ...overrides,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

describe("CollectionEnvironmentModule", () => {
  const onStartSetup = vi.fn().mockResolvedValue(undefined);
  const renderModule = () => render(<ToastProvider><CollectionEnvironmentModule onStartSetup={onStartSetup} /></ToastProvider>);

  beforeEach(() => {
    vi.clearAllMocks();
    window.history.replaceState({}, "", "/?menu=environment");
    vi.mocked(fetchCollectionEnvironment).mockResolvedValue(snapshot());
    vi.mocked(launchDedicatedChrome).mockResolvedValue({ ok: true, task_id: "chrome-launch-1", console_url: "/browser-console/" });
    vi.mocked(checkPlatformAccount).mockResolvedValue({ ok: true, platform: "x", status: "verified" });
    vi.mocked(launchPlatformLogin).mockResolvedValue({ ok: true, platform: "x", start_url: "https://x.com/login", console_url: "/browser-console/" });
    vi.mocked(loadBrowserVisibility).mockResolvedValue({ headless_during_fetch: true });
    vi.mocked(saveBrowserVisibility).mockResolvedValue(undefined);
    vi.mocked(fetchAuthRecovery).mockResolvedValue({ ok: true, incident: recoveryIncident() });
    vi.mocked(openAuthRecoveryConsole).mockResolvedValue({ ok: true, console_url: "/browser-console/", incident: recoveryIncident() });
    vi.mocked(openPendingAuthRecoveryConsole).mockResolvedValue({ ok: true, console_url: "/browser-console/", incident: recoveryIncident() });
    vi.mocked(verifyAuthRecovery).mockResolvedValue({ ok: false, verification: { status: "failed", code: "platform_session_not_ready", checked_at: "2026-07-26T08:30:00Z" }, incident: recoveryIncident() });
    vi.mocked(verifyPendingAuthRecovery).mockResolvedValue({ ok: true, verification: { status: "passed", method: "platform_session", checked_at: "2026-07-27T08:30:00Z" }, incident: recoveryIncident({ status: "resolved" }) });
    onStartSetup.mockClear();
  });

  it("loads an auth recovery from the deep link and opens its container console", async () => {
    const user = userEvent.setup();
    window.history.replaceState({}, "", "/?menu=environment&auth_recovery=incident-1");
    renderModule();

    expect(await screen.findByRole("region", { name: "登录恢复" })).toHaveTextContent("X");
    expect(fetchAuthRecovery).toHaveBeenCalledWith("incident-1", expect.any(AbortSignal));
    await user.click(screen.getByRole("button", { name: "打开容器浏览器" }));
    expect(openAuthRecoveryConsole).toHaveBeenCalledWith("incident-1");
    expect(await screen.findByText("请在容器内完成扫码、密码、MFA 或二次确认；完成后关闭控制台并点击完成并验证。", { selector: "p" })).toBeInTheDocument();
  });

  it("keeps a recovery actionable when server verification reports login not ready", async () => {
    const user = userEvent.setup();
    window.history.replaceState({}, "", "/?menu=environment&auth_recovery=incident-1");
    renderModule();
    await screen.findByRole("region", { name: "登录恢复" });

    await user.click(screen.getByRole("button", { name: "完成并验证" }));

    expect(verifyAuthRecovery).toHaveBeenCalledWith("incident-1");
    expect(await screen.findByText("登录或安全验证尚未完成")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "打开容器浏览器" })).toBeEnabled();
  });

  it("shows recovered task count and refreshes the passive environment snapshot after successful verification", async () => {
    const user = userEvent.setup();
    window.history.replaceState({}, "", "/?menu=environment&auth_recovery=incident-1");
    vi.mocked(verifyAuthRecovery).mockResolvedValueOnce({ ok: true, verification: { status: "passed", method: "platform_session", checked_at: "2026-07-26T08:30:00Z" }, incident: recoveryIncident({ status: "resolved", recovered_task_count: 4, resolved_at: "2026-07-26T08:30:00Z" }) });
    renderModule();
    await screen.findByRole("region", { name: "登录恢复" });

    await user.click(screen.getByRole("button", { name: "完成并验证" }));

    expect(await screen.findByText("已恢复，已恢复 4 个任务。")).toBeInTheDocument();
    await waitFor(() => expect(fetchCollectionEnvironment).toHaveBeenCalledTimes(2));
  });

  it("offers an explicit entry into the first-time setup flow", async () => {
    const user = userEvent.setup();
    renderModule();

    await user.click(await screen.findByRole("button", { name: "运行首次设置" }));

    expect(onStartSetup).toHaveBeenCalledTimes(1);
  });

  it("keeps the modal closed upstream and reports reset failures", async () => {
    const user = userEvent.setup();
    onStartSetup.mockRejectedValueOnce(new Error("setup reset failed"));
    renderModule();

    await user.click(await screen.findByRole("button", { name: "运行首次设置" }));

    expect(await screen.findByRole("status")).toHaveTextContent("首次设置启动失败：setup reset failed");
  });

  it("loads and saves the browser fetch mode from collection environment", async () => {
    const user = userEvent.setup();
    renderModule();
    const mode = await screen.findByRole("combobox", { name: "采集浏览器运行方式" });
    expect(mode).toHaveValue("headless");

    await user.selectOptions(mode, "visible");

    await waitFor(() => expect(saveBrowserVisibility).toHaveBeenCalledWith(false));
    expect(mode).toHaveValue("visible");
    expect(screen.getByRole("status")).toHaveTextContent("下次启动或修复采集会话时生效");
  });

  it("renders exactly eight compact component cards, only one component action, and six backend accounts with both actions", async () => {
    renderModule();
    const components = await screen.findByRole("region", { name: "系统组件" });
    componentNames.forEach((name) => expect(within(components).getByText(name)).toBeInTheDocument());
    expect(within(components).getAllByRole("article")).toHaveLength(8);
    expect(within(components).getAllByRole("button")).toHaveLength(1);
    expect(within(components).getByRole("button", { name: "打开专用 Chrome" })).toBeInTheDocument();

    const table = screen.getByRole("table", { name: "平台账号" });
    expect(within(table).getAllByRole("row")).toHaveLength(7);
    platforms.forEach(([, label]) => {
      const row = within(table).getByRole("row", { name: new RegExp(label) });
      expect(within(row).getByRole("button", { name: `状态检查 ${label}` })).toBeInTheDocument();
      expect(within(row).getByRole("button", { name: `去登录 ${label}` })).toBeInTheDocument();
    });
    expect(screen.queryByText("Google")).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("8 个来源受登录状态影响");
  });

  it("shows the aggregate container failure and an actionable next step", async () => {
    vi.mocked(fetchCollectionEnvironment).mockResolvedValueOnce({
      ...snapshot(),
      runtime_health: {
        status: "unhealthy",
        reasons: ["opencli daemon unavailable"],
        checked_at: "2026-07-22T12:00:00Z",
      },
    });
    renderModule();

    const alert = (await screen.findByText("容器运行异常")).closest('[role="alert"]');
    if (!alert) throw new Error("runtime health alert missing");
    expect(alert).toHaveTextContent("容器运行异常");
    expect(alert).toHaveTextContent("opencli daemon unavailable");
    expect(alert).toHaveTextContent("请查看容器日志");
  });

  it("projects open provider and half-open platform circuits without exposing runtime internals", async () => {
    vi.mocked(fetchCollectionEnvironment).mockResolvedValueOnce({
      ...snapshot(),
      runtime_circuits: [
        {
          scope_kind: "provider",
          scope_key: "cloud-llm",
          state: "open",
          consecutive_failures: 3,
          reason_code: "provider_unavailable",
          opened_at: "2026-07-22T12:00:00Z",
          next_probe_at: "2026-07-22T12:15:00Z",
          last_failure_at: "2026-07-22T12:00:00Z",
          last_success_at: null,
          updated_at: "2026-07-22T12:00:00Z",
          user_action: "config_required",
          probe_owner: "worker-42",
          raw_error: "upstream secret failure",
        },
        {
          scope_kind: "platform",
          scope_key: "x",
          state: "half_open",
          consecutive_failures: 1,
          reason_code: "login_required",
          opened_at: "2026-07-22T12:00:00Z",
          next_probe_at: null,
          last_failure_at: "2026-07-22T12:00:00Z",
          last_success_at: null,
          updated_at: "2026-07-22T12:05:00Z",
          user_action: "login_required",
          probe_owner: "worker-17",
          raw_error: "cookie=super-secret",
        },
        {
          scope_kind: "platform",
          scope_key: "rss:rss-A",
          source_id: "rss-A",
          source_name: "测试订阅 A",
          state: "open",
          consecutive_failures: 3,
          reason_code: "network_timeout",
          opened_at: "2026-07-22T12:00:00Z",
          next_probe_at: "2026-07-22T12:15:00Z",
          user_action: "none",
        },
      ],
    } as unknown as CollectionEnvironmentSnapshot);
    renderModule();

    const protection = await screen.findByRole("region", { name: "运行保护" });
    expect(within(protection).getByText("供应商 · cloud-llm")).toBeInTheDocument();
    expect(within(protection).getAllByText("已熔断")).toHaveLength(2);
    expect(within(protection).getByText("RSS 订阅 · 测试订阅 A（rss-A）")).toBeInTheDocument();
    expect(within(protection).getByText("采集请求超时")).toBeInTheDocument();
    expect(within(protection).queryByText("平台 · rss:rss-A")).not.toBeInTheDocument();
    expect(within(protection).getByText("检查配置")).toBeInTheDocument();
    expect(within(protection).getByText("平台 · x")).toBeInTheDocument();
    expect(within(protection).getByText("恢复探测中")).toBeInTheDocument();
    expect(within(protection).getByText("需人工恢复")).toBeInTheDocument();
    expect(within(protection).getByText("需要登录")).toBeInTheDocument();
    expect(screen.queryByText("worker-42")).not.toBeInTheDocument();
    expect(screen.queryByText("upstream secret failure")).not.toBeInTheDocument();
    expect(screen.queryByText("cookie=super-secret")).not.toBeInTheDocument();
  });

  it("discovers a pending auth recovery from the environment snapshot and performs its provided actions", async () => {
    const user = userEvent.setup();
    const recovery = pendingRecovery();
    vi.mocked(fetchCollectionEnvironment).mockResolvedValueOnce({
      ...snapshot(),
      runtime_circuits: [{
        scope_kind: "platform",
        scope_key: "x",
        state: "open",
        consecutive_failures: 1,
        reason_code: "login_required",
        opened_at: "2026-07-27T08:00:00Z",
        next_probe_at: null,
        last_failure_at: "2026-07-27T08:00:00Z",
        last_success_at: null,
        updated_at: "2026-07-27T08:00:00Z",
        user_action: "login_required",
      }],
      pending_auth_recoveries: [recovery],
    });
    renderModule();

    const protection = await screen.findByRole("region", { name: "运行保护" });
    expect(within(protection).getByText("待处理登录恢复")).toBeInTheDocument();
    expect(within(protection).getByText("影响 3 个任务")).toBeInTheDocument();
    expect(fetchAuthRecovery).not.toHaveBeenCalled();

    await user.click(within(protection).getByRole("button", { name: "打开授权面板" }));
    expect(openPendingAuthRecoveryConsole).toHaveBeenCalledWith(recovery.open_console_url);
    expect(await screen.findByRole("dialog", { name: "容器浏览器控制台" })).toBeInTheDocument();

    await user.click(within(protection).getByRole("button", { name: "完成并验证" }));
    expect(verifyPendingAuthRecovery).toHaveBeenCalledWith(recovery.verify_url);
    await waitFor(() => expect(fetchCollectionEnvironment).toHaveBeenCalledTimes(2));
  });

  it("refreshes only the passive snapshot and opens/focuses dedicated Chrome only from its card", async () => {
    const user = userEvent.setup();
    renderModule();
    await screen.findByText("OpenCLI Bridge");
    await user.click(screen.getByRole("button", { name: "刷新状态" }));
    await waitFor(() => expect(fetchCollectionEnvironment).toHaveBeenCalledTimes(2));
    expect(checkPlatformAccount).not.toHaveBeenCalled();
    expect(launchDedicatedChrome).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "打开专用 Chrome" }));
    expect(launchDedicatedChrome).toHaveBeenCalledTimes(1);
    const consoleDialog = await screen.findByRole("dialog", { name: "容器浏览器控制台" });
    expect(within(consoleDialog).getByTitle("InfoOS 容器浏览器")).toHaveAttribute("src", "/browser-console/");
  });

  it("opens the account dialog immediately, locks only that check, then refreshes persisted status", async () => {
    const user = userEvent.setup();
    const pending = deferred<{ ok: boolean; platform: string; status: string }>();
    vi.mocked(checkPlatformAccount).mockReturnValueOnce(pending.promise);
    vi.mocked(fetchCollectionEnvironment).mockResolvedValueOnce(snapshot()).mockResolvedValueOnce(snapshot({ douyin: "verified" }));
    renderModule();
    const check = await screen.findByRole("button", { name: "状态检查 抖音" });
    await user.click(check);
    const dialog = screen.getByRole("dialog", { name: "账号状态检查 · 抖音" });
    expect(within(dialog).getByRole("status")).toHaveTextContent("正在检查");
    expect(check).toBeDisabled();
    expect(screen.getByRole("button", { name: "状态检查 X" })).not.toBeDisabled();
    pending.resolve({ ok: true, platform: "douyin", status: "verified" });
    await waitFor(() => expect(fetchCollectionEnvironment).toHaveBeenCalledTimes(2));
    expect(within(dialog).getByText("已登录")).toBeInTheDocument();
    expect(within(dialog).getByText("dedicated_chrome")).toBeInTheDocument();
    expect(within(dialog).getByText("session-dy")).toBeInTheDocument();
  });

  it("keeps search/filter state and supports direct login plus login from the status dialog", async () => {
    const user = userEvent.setup();
    renderModule();
    await screen.findByText("小红书");
    await user.type(screen.getByLabelText("搜索平台账号"), "小红书");
    await user.selectOptions(screen.getByLabelText("账号状态筛选"), "verified");
    expect(screen.getByRole("row", { name: /小红书/ })).toBeInTheDocument();
    expect(screen.queryByRole("row", { name: /YouTube/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "去登录 小红书" }));
    expect(launchPlatformLogin).toHaveBeenCalledWith("xiaohongshu");
    const browserConsole = await screen.findByRole("dialog", { name: "容器浏览器控制台" });
    await user.click(within(browserConsole).getByRole("button", { name: "关闭对话框" }));
    await user.click(screen.getByRole("button", { name: "状态检查 小红书" }));
    const dialog = await screen.findByRole("dialog", { name: "账号状态检查 · 小红书" });
    await user.click(within(dialog).getByRole("button", { name: "在专用 Chrome 中登录" }));
    expect(launchPlatformLogin).toHaveBeenLastCalledWith("xiaohongshu");
    expect(await screen.findByRole("dialog", { name: "容器浏览器控制台" })).toBeInTheDocument();
    expect(screen.getByLabelText("搜索平台账号")).toHaveValue("小红书");
    expect(screen.getByLabelText("账号状态筛选")).toHaveValue("verified");
  });

  it("reports one platform check failure, refreshes its persisted result, and unlocks unrelated rows", async () => {
    const user = userEvent.setup();
    vi.mocked(checkPlatformAccount).mockRejectedValueOnce(new Error("会话探针失败"));
    vi.mocked(fetchCollectionEnvironment).mockResolvedValueOnce(snapshot()).mockResolvedValueOnce(snapshot({ youtube: "failed" }));
    renderModule();
    await user.click(await screen.findByRole("button", { name: "状态检查 YouTube" }));
    const dialog = screen.getByRole("dialog", { name: "账号状态检查 · YouTube" });
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("会话探针失败");
    expect(within(dialog).getByText("检查失败")).toBeInTheDocument();
    expect(fetchCollectionEnvironment).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("button", { name: "状态检查 X" })).not.toBeDisabled();
  });

  it("allows filtering persisted failed account checks", async () => {
    const user = userEvent.setup();
    vi.mocked(fetchCollectionEnvironment).mockResolvedValue(snapshot({ youtube: "failed" }));
    renderModule();
    await user.selectOptions(await screen.findByLabelText("账号状态筛选"), "failed");
    expect(screen.getByRole("row", { name: /YouTube/ })).toBeInTheDocument();
    expect(screen.queryByRole("row", { name: /小红书/ })).not.toBeInTheDocument();
  });
});
