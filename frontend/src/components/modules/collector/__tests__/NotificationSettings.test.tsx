import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NotificationSettings } from "../collectionEnvironment/NotificationSettings";
import { SendRecoveryQrButton } from "../collectionEnvironment/SendRecoveryQrButton";
import { loadNotifications, saveNotifications, testNotifications, sendRecoveryQr, loadFeishuConnection, connectFeishu, cancelFeishuConnection } from "../../../../lib/api/notifications";
import type { NotificationSettings as Settings } from "../../../../lib/types/notifications";

vi.mock("../../../../lib/api/notifications", () => ({
  loadNotifications: vi.fn(), saveNotifications: vi.fn(), testNotifications: vi.fn(), sendRecoveryQr: vi.fn(), loadFeishuConnection: vi.fn(), connectFeishu: vi.fn(), cancelFeishuConnection: vi.fn(),
}));
const settings: Settings = {
  config: { content_push: { daily_brief_digest: true }, enabled: true, channels: { feishu: { enabled: true, receive_id: "ou_fixture" } },
    quiet_hours: { start: "23:00", end: "08:00", timezone: "Asia/Shanghai" },
    auth_qr: { codes_per_round: 3, round_interval_hours: 4, max_rounds_per_day: 4 },
    agent_watchdog: { evaluate_stall_hours: 30, opportunities_stall_hours: 9,
      evaluate_since: "2026-09-16T00:00:00Z", opportunities_since: "2026-10-14T00:00:00Z" } },
  readiness: { feishu: true, email: false }, latest_results: [],
};

describe("通知设置", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(loadNotifications).mockResolvedValue(structuredClone(settings));
    vi.mocked(saveNotifications).mockResolvedValue(structuredClone(settings));
    vi.mocked(loadFeishuConnection).mockResolvedValue({ step: "create_app", status: "idle", expires_at: null, error_code: null, qr_version: 0 });
    vi.mocked(connectFeishu).mockResolvedValue({ step: "create_app", status: "waiting_scan", expires_at: "2026-10-22T00:00:00Z", error_code: null, qr_version: 1 });
    vi.mocked(cancelFeishuConnection).mockResolvedValue({ step: "create_app", status: "idle", expires_at: null, error_code: null, qr_version: 0 });
    vi.mocked(testNotifications).mockResolvedValue({ results: [{ channel: "feishu", status: "sent", error_code: null }] });
    vi.mocked(sendRecoveryQr).mockResolvedValue({ ok: true, round: { status: "capturing" } });
  });
  it("扫码连接显示步骤和二维码，可取消并保留保存与测试", async () => {
    const user = userEvent.setup();
    render(<NotificationSettings />);
    await screen.findByRole("button", { name: "扫码连接飞书" });
    expect(screen.queryByLabelText("App Secret")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("手机号")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "保存通知设置" }));
    await waitFor(() => expect(saveNotifications).toHaveBeenCalled());
    expect(vi.mocked(saveNotifications).mock.calls[0][0].channels!.feishu).toEqual({ enabled: true, receive_id: "ou_fixture" });
    await user.click(screen.getByRole("button", { name: "扫码连接飞书" }));
    expect(await screen.findByText("第 1 步：用飞书 App 扫码创建 InfoOS 通知应用")).toBeInTheDocument();
    expect(screen.getByAltText("飞书连接二维码")).toHaveAttribute("src", "/api/settings/notifications/feishu/connect/qr.png?v=1");
    await user.click(screen.getByRole("button", { name: "取消连接" }));
    await waitFor(() => expect(cancelFeishuConnection).toHaveBeenCalled());
    await user.click(screen.getByRole("button", { name: "发送测试" }));
    expect(await screen.findByText("feishu：sent")).toBeInTheDocument();
  });
  it("恢复第二步并轮询完成后刷新设置", async () => {
    vi.mocked(loadFeishuConnection).mockResolvedValueOnce({ step: "authorize_user", status: "waiting_scan", expires_at: "2026-10-22T00:00:00Z", error_code: null, qr_version: 2 })
      .mockResolvedValue({ step: "done", status: "completed", expires_at: null, error_code: null, qr_version: 2 });
    render(<NotificationSettings />);
    expect(await screen.findByText("第 2 步：扫码登录授权，扫码即绑定你的飞书账号，请勿转发")).toBeInTheDocument();
    expect(await screen.findByText("飞书连接成功，测试通知已入队。", {}, { timeout: 4000 })).toBeInTheDocument();
    expect(loadNotifications).toHaveBeenCalledTimes(2);
  });
  it("显示连接失败错误码", async () => {
    vi.mocked(loadFeishuConnection).mockResolvedValue({ step: "create_app", status: "failed", expires_at: null, error_code: "lark_network", qr_version: 0 });
    render(<NotificationSettings />);
    expect(await screen.findByText("飞书连接失败（lark_network），请重新扫码。")).toBeInTheDocument();
  });
  it("手动二维码按钮排队，并显示进行中的轮次", async () => {
    const user = userEvent.setup();
    render(<SendRecoveryQrButton incidentId="fixture" platform="xiaohongshu" />);
    await user.click(screen.getByRole("button", { name: "发送二维码到飞书" }));
    expect(sendRecoveryQr).toHaveBeenCalledWith("fixture");
    expect(await screen.findByText("二维码轮次已启动或正在进行，请查看飞书。")).toBeInTheDocument();
  });
});
