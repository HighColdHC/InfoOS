import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { loadPlatformCapabilities, loadSetupState, postSetupReset } from "./lib/api";

const loadCollectorStatus = vi.fn();

vi.mock("./lib/api/collectorStatus", () => ({
  loadCollectorStatus: () => loadCollectorStatus(),
}));

vi.mock("./lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./lib/api")>();
  return {
    ...actual,
    loadSetupState: vi.fn().mockResolvedValue({ current_step: "complete" }),
    loadPlatformCapabilities: vi.fn().mockResolvedValue([]),
    postSetupReset: vi.fn().mockResolvedValue({ current_step: "fresh" }),
  };
});

describe("collector application shell", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/");
    loadCollectorStatus.mockReset();
    loadCollectorStatus.mockResolvedValue({
      service_status: "ready",
      latest_collection_at: "2026-07-16T10:00:00Z",
      reasons: [],
    });
    vi.mocked(loadSetupState).mockReset().mockResolvedValue({ current_step: "complete" } as never);
    vi.mocked(loadPlatformCapabilities).mockReset().mockResolvedValue([]);
    vi.mocked(postSetupReset).mockReset().mockResolvedValue({ current_step: "fresh" } as never);
  });

  it("opens card preview by default and exposes only the seven collector menus", async () => {
    render(<App refreshIntervalMs={0} />);

    // 首次 lazy import 包含管理台依赖的转换；开发机并发测试时可能超过默认 1 秒。
    expect(await screen.findByRole("heading", { name: "信息卡" }, { timeout: 4_000 })).toBeInTheDocument();
    for (const label of ["信息卡", "信息源", "采集任务", "采集环境", "Token 认证", "账号与安全", "导出与日志"]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
    for (const oldLabel of ["今日", "信号", "决策", "工作室", "系统"]) {
      expect(screen.queryByRole("button", { name: oldLabel })).not.toBeInTheDocument();
    }
  });

  it("restores the active page from the menu URL and persists navigation", async () => {
    window.history.replaceState({}, "", "/?menu=tokens");
    const user = userEvent.setup();
    render(<App refreshIntervalMs={0} />);

    expect(await screen.findByRole("heading", { name: "Token 认证" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "采集任务" }));
    expect(screen.getByRole("heading", { name: "采集任务" })).toBeInTheDocument();
    expect(new URLSearchParams(window.location.search).get("menu")).toBe("tasks");
  });

  it("loads only passive collector status on initial render", async () => {
    render(<App refreshIntervalMs={0} />);
    expect(await screen.findByText(/最近采集成功：7月16日 \d{2}:00/)).toBeInTheDocument();
    expect(loadCollectorStatus).toHaveBeenCalledTimes(1);
  });

  it("resets setup state before opening the first-time setup modal", async () => {
    const user = userEvent.setup();
    vi.mocked(loadSetupState)
      .mockResolvedValueOnce({ current_step: "complete" } as never)
      .mockResolvedValueOnce({
        current_step: "fresh",
        extension: { loaded: false },
        session: { context_id: null },
        accounts: {},
        first_fetch: { ok: null },
      } as never);
    render(<App refreshIntervalMs={0} />);

    await user.click(await screen.findByRole("button", { name: "采集环境" }));
    await user.click(await screen.findByRole("button", { name: "运行首次设置" }));

    expect(postSetupReset).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole("heading", { name: "InfoOS 首次设置" })).toBeInTheDocument();
  });
});
