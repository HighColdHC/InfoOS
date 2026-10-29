import { act, render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { vi } from "vitest";
import { OnboardingModal } from "../OnboardingModal";

const mocks = vi.hoisted(() => ({
  loadSetupState: vi.fn(),
  loadPlatformCapabilities: vi.fn(),
  postSetupLaunch: vi.fn(),
  postSetupVerifyExtension: vi.fn(),
  postSetupVerifyPlatform: vi.fn(),
  postSetupSkipPlatform: vi.fn(),
  postSetupFirstFetch: vi.fn(),
  postSetupDismiss: vi.fn(),
}));

// Path is 3 levels up because the test file lives in __tests__/
vi.mock("../../../lib/api", () => ({
  loadSetupState: mocks.loadSetupState,
  loadPlatformCapabilities: mocks.loadPlatformCapabilities,
  postSetupLaunch: mocks.postSetupLaunch,
  postSetupVerifyExtension: mocks.postSetupVerifyExtension,
  postSetupVerifyPlatform: mocks.postSetupVerifyPlatform,
  postSetupSkipPlatform: mocks.postSetupSkipPlatform,
  postSetupFirstFetch: mocks.postSetupFirstFetch,
  postSetupDismiss: mocks.postSetupDismiss,
}));

function makeCompleteState() {
  return {
    version: 1,
    current_step: "complete" as const,
    completed_steps: ["install_extension", "bind_profile", "first_fetch"],
    chrome: { binary_path: null, version: null, profile_dir: null, running: false },
    extension: { loaded: true, id: "opencli", manifest_version: 3, verified_at: null },
    session: { context_id: "ctx-abc", alias: "ctx-abc", bound_at: null },
    accounts: {},
    first_fetch: { ok: true, fetched_count: 1, ran_at: null },
    wizard_opens: 0,
    wizard_last_seen_step: null,
  };
}

function makeFreshState() {
  return {
    version: 1,
    current_step: "fresh" as const,
    completed_steps: [],
    chrome: { binary_path: null, version: null, profile_dir: null, running: false },
    extension: { loaded: false, id: null, manifest_version: null, verified_at: null },
    session: { context_id: null, alias: null, bound_at: null },
    accounts: {},
    first_fetch: { ok: null, fetched_count: null, ran_at: null },
    wizard_opens: 0,
    wizard_last_seen_step: null,
  };
}

function resetMocks() {
  mocks.loadSetupState.mockReset();
  mocks.loadPlatformCapabilities.mockReset();
  mocks.loadPlatformCapabilities.mockResolvedValue([
    { platform: "x", label: "X", configurable: true, requires_browser_session: true, source_types: [], cadence_options: [] },
    { platform: "scys", label: "生财有术", configurable: true, requires_browser_session: true, source_types: [], cadence_options: [] },
  ]);
  mocks.postSetupLaunch.mockReset();
  mocks.postSetupVerifyExtension.mockReset();
  mocks.postSetupVerifyPlatform.mockReset();
  mocks.postSetupSkipPlatform.mockReset();
  mocks.postSetupFirstFetch.mockReset();
  mocks.postSetupDismiss.mockReset();
}

describe("OnboardingModal tour mode", () => {
  beforeEach(() => {
    localStorage.clear();
    resetMocks();
  });

  it("shows step 1 of 3 when setup is already complete", async () => {
    mocks.loadSetupState.mockResolvedValue(makeCompleteState());
    render(<OnboardingModal onClose={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByText("第 1 步 / 共 3 步")).toBeInTheDocument();
    });
    expect(screen.getByText("采集与信息卡")).toBeInTheDocument();
  });

  it("advances through 3 steps", async () => {
    mocks.loadSetupState.mockResolvedValue(makeCompleteState());
    render(<OnboardingModal onClose={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByText("第 1 步 / 共 3 步")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /下一步/ }));
    expect(screen.getByText("第 2 步 / 共 3 步")).toBeInTheDocument();
    expect(screen.getByText("外部 agent 依据关注画像经 /mcp 写回评估，InfoOS 汇总为日报。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /下一步/ }));
    expect(screen.getByText("第 3 步 / 共 3 步")).toBeInTheDocument();
    expect(screen.getByText("在 Web 管理来源、任务、环境、Token 与导出，打开深链接阅读或轻量浏览信息卡。")).toBeInTheDocument();
  });

  it("'跳过' writes localStorage and calls onClose", async () => {
    mocks.loadSetupState.mockResolvedValue(makeCompleteState());
    const onClose = vi.fn();
    render(<OnboardingModal onClose={onClose} />);
    await waitFor(() => {
      expect(screen.getByText("第 1 步 / 共 3 步")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: "跳过" }));
    expect(localStorage.getItem("infoos.onboarding.v1.completed")).toBe("true");
    expect(onClose).toHaveBeenCalledOnce();
  });
});

describe("OnboardingModal setup mode", () => {
  beforeEach(() => {
    localStorage.clear();
    resetMocks();
  });

  it("renders the helper + 3 setup cards when current_step is fresh", async () => {
    mocks.loadSetupState.mockResolvedValue(makeFreshState());
    render(<OnboardingModal onClose={vi.fn()} />);
    expect(await screen.findByText("专用 Chrome 窗口")).toBeInTheDocument();
    expect(screen.getByText("连接 OpenCLI Bridge 扩展")).toBeInTheDocument();
    expect(screen.getByText("登录平台账号")).toBeInTheDocument();
    expect(screen.getByText("跑一次测试抓取")).toBeInTheDocument();
  });


  it.each([
    ["跳过", mocks.postSetupSkipPlatform, "跳过平台失败"],
    ["验证登录持久化", mocks.postSetupVerifyPlatform, "平台验证失败"],
    ["跑一次试试", mocks.postSetupFirstFetch, "测试抓取失败"],
  ])("shows an explicit error when %s fails", async (name, action, prefix) => {
    mocks.loadSetupState.mockResolvedValue(makeFreshState());
    action.mockRejectedValueOnce(new Error("network unavailable"));
    render(<OnboardingModal onClose={vi.fn()} />);
    fireEvent.click((await screen.findAllByRole("button", { name }))[0]);
    expect(await screen.findByRole("alert")).toHaveTextContent(`${prefix}：network unavailable`);
  });

  it("invokes postSetupLaunch when the launch helper button is clicked", async () => {
    mocks.loadSetupState.mockResolvedValue(makeFreshState());
    mocks.postSetupLaunch.mockResolvedValue({ ok: true, console_url: "/browser-console/" });
    render(<OnboardingModal onClose={vi.fn()} />);
    const launchButton = await screen.findByRole("button", { name: "启动专用 Chrome" });
    fireEvent.click(launchButton);
    await waitFor(() => {
      expect(mocks.postSetupLaunch).toHaveBeenCalledTimes(1);
    });
    expect(screen.getByTitle("InfoOS 容器浏览器")).toHaveAttribute("src", "/browser-console/");
  });

  it("does not treat the Chrome launch helper as a pending setup card", async () => {
    mocks.loadSetupState.mockResolvedValue(makeFreshState());
    render(<OnboardingModal onClose={vi.fn()} />);
    expect(await screen.findByText("专用 Chrome 窗口")).toBeInTheDocument();
    expect(screen.queryByText("启动 InfoOS Chrome")).not.toBeInTheDocument();
    expect(document.querySelectorAll(".setup-card")).toHaveLength(3);
  });

  it("persists setup dismissal when the user skips setup mode", async () => {
    mocks.loadSetupState.mockResolvedValue(makeFreshState());
    mocks.postSetupDismiss.mockResolvedValue({ current_step: "skipped" });
    const onClose = vi.fn();
    render(<OnboardingModal onClose={onClose} />);

    fireEvent.click(await screen.findByRole("button", { name: "跳过,稍后设置" }));

    await waitFor(() => {
      expect(mocks.postSetupDismiss).toHaveBeenCalledTimes(1);
    });
    expect(localStorage.getItem("infoos.onboarding.v1.completed")).toBe("true");
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("only marks login setup done after persistence is verified", async () => {
    const state = makeFreshState();
    state.accounts = {
      x: {
        status: "verified",
        last_checked: "now",
        error: null,
        persisted: false,
        persistence_checked_at: "now",
        persistence_stage: "post_restart_probe",
        persistence_error: "重启后验证失败",
      },
    };
    mocks.loadSetupState.mockResolvedValue(state);

    render(<OnboardingModal onClose={vi.fn()} />);

    const loginCard = (await screen.findByText("登录平台账号")).closest(".setup-card");
    expect(loginCard).toHaveAttribute("data-status", "pending");
    expect(screen.getByText("登录状态：已验证")).toBeInTheDocument();
    expect(within(screen.getByText("X").closest("li")!).getByText("持久化状态：未验证")).toBeInTheDocument();
  });

  it("marks login setup done when a platform persistence check succeeds", async () => {
    const state = makeFreshState();
    state.accounts = {
      x: {
        status: "verified",
        last_checked: "now",
        error: null,
        persisted: true,
        persistence_checked_at: "now",
        persistence_stage: "verified",
        persistence_error: null,
      },
    };
    mocks.loadSetupState.mockResolvedValue(state);

    render(<OnboardingModal onClose={vi.fn()} />);

    const loginCard = (await screen.findByText("登录平台账号")).closest(".setup-card");
    expect(loginCard).toHaveAttribute("data-status", "done");
    expect(within(screen.getByText("X").closest("li")!).getByText("持久化状态：已验证")).toBeInTheDocument();
  });

  it("invokes persistence verification and disables that platform while busy", async () => {
    const state = makeFreshState();
    state.accounts = { x: { status: "unknown", last_checked: null, error: null } };
    mocks.loadSetupState.mockResolvedValue(state);
    let resolveVerification: (() => void) | undefined;
    mocks.postSetupVerifyPlatform.mockImplementation(() => new Promise<void>((resolve) => {
      resolveVerification = resolve;
    }));

    render(<OnboardingModal onClose={vi.fn()} />);

    const xRow = (await screen.findByText("X")).closest("li")!;
    const button = await within(xRow).findByRole("button", { name: "验证登录持久化" });
    fireEvent.click(button);

    await waitFor(() => {
      expect(mocks.postSetupVerifyPlatform).toHaveBeenCalledWith("x");
    });
    expect(within(xRow).getByRole("button", { name: "正在验证登录持久化..." })).toBeDisabled();

    await act(async () => {
      resolveVerification?.();
    });
    await waitFor(() => {
      expect(within(screen.getByText("X").closest("li")!).getByRole("button", { name: "验证登录持久化" })).toBeEnabled();
    });
  });

  it("renders persistence stage and failure details", async () => {
    const state = makeFreshState();
    state.accounts = {
      x: {
        status: "failed",
        last_checked: "now",
        error: "Dedicated Chrome did not restart gracefully",
        persisted: false,
        persistence_checked_at: "now",
        persistence_stage: "restart",
        persistence_error: "Dedicated Chrome did not restart gracefully",
      },
    };
    mocks.loadSetupState.mockResolvedValue(state);

    render(<OnboardingModal onClose={vi.fn()} />);

    expect(await screen.findByText("登录状态：验证失败")).toBeInTheDocument();
    expect(screen.getByText("验证阶段：安全重启专用 Chrome")).toBeInTheDocument();
    expect(screen.getByText("持久化错误：专用 Chrome 未能正常重启")).toBeInTheDocument();
    expect(screen.getByText(/此主动操作会安全重启一次 InfoOS 专用 Chrome/)).toBeInTheDocument();
  });

  it("renders SCYS as a setup platform", async () => {
    const state = makeFreshState();
    state.accounts = { scys: { status: "unknown", last_checked: null, error: null } };
    mocks.loadSetupState.mockResolvedValue(state);
    mocks.postSetupVerifyPlatform.mockResolvedValue({ ok: true, status: "verified", persisted: true });

    render(<OnboardingModal onClose={vi.fn()} />);

    expect(await screen.findByText("生财有术")).toBeInTheDocument();
    const verifyButtons = await screen.findAllByRole("button", { name: /验证登录持久化/ });
    fireEvent.click(verifyButtons[verifyButtons.length - 1]);

    await waitFor(() => {
      expect(mocks.postSetupVerifyPlatform).toHaveBeenCalledWith("scys");
    });
  });

  it("displays verify-extension hint when the bridge is not connected", async () => {
    mocks.loadSetupState.mockResolvedValue(makeFreshState());
    mocks.postSetupVerifyExtension.mockResolvedValue({
      ok: false,
      status: "no-connection",
      current_step: "install_extension",
      hint: "No Browser Bridge context is connected.",
    });

    render(<OnboardingModal onClose={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: /检查连接/ }));

    expect(await screen.findByText("Browser Bridge 扩展未连接")).toBeInTheDocument();
  });
});
