import type { ReactElement } from "react";
import type { PlatformCapability, SetupAccountState, SetupState } from "../../../lib/types";
import { Modal } from "../Modal";
import { Button } from "../primitives/Button";
import { SetupCard } from "../SetupCard";
import { BrowserConsoleFrame } from "../BrowserConsoleFrame";
import { useOnboardingSetup } from "./useOnboardingSetup";

const LOGIN_STATUS_LABELS: Record<string, string> = {
  unknown: "未检查",
  needs_login: "需要登录",
  verified: "已验证",
  skipped: "已跳过",
  failed: "验证失败",
};

const PERSISTENCE_STAGE_LABELS: Record<string, string> = {
  verifying: "正在验证",
  platform: "平台不支持",
  precondition: "检查专用浏览器绑定",
  pre_restart_probe: "重启前登录验证",
  restart: "安全重启专用 Chrome",
  binding: "验证重启后的浏览器绑定",
  passive_cookie: "检查重启后的登录状态",
  post_restart_probe: "重启后登录验证",
  verified: "持久化验证通过",
  lock: "等待浏览器验证锁",
  internal: "验证过程失败",
  state_persistence: "保存验证状态",
  passive_logged_out: "检测到已退出登录",
};

function loginStatusLabel(status: string): string {
  return LOGIN_STATUS_LABELS[status] ?? status;
}

function persistenceStageLabel(stage: string | null): string {
  return stage ? (PERSISTENCE_STAGE_LABELS[stage] ?? stage) : "尚未验证";
}

function persistenceError(account: SetupAccountState | undefined): string | null {
  return account?.persistence_error ?? account?.error ?? null;
}

export function SetupMode({
  state,
  setState,
  platforms,
  onClose,
}: {
  state: SetupState;
  setState: (s: SetupState) => void;
  platforms: PlatformCapability[];
  onClose: () => void;
}): ReactElement {
  const {
    state: operation,
    dispatch,
    dismiss,
    launchChrome,
    verifyExtension,
    probePlatform,
    skipPlatform,
    runFirstFetch,
  } = useOnboardingSetup(state, setState, onClose);
  const {
    chromeError,
    extensionBusy,
    extensionError,
    firstFetchQueued,
    boundContextId,
    platformBusy,
    browserConsoleUrl,
  } = operation;
  return (
    <Modal open={true} title="InfoOS 首次设置" onClose={dismiss}>
      <div className="onboarding setup-mode onboarding-card">
        {operation.actionError ? <p className="setup-card-error" role="alert">{operation.actionError}</p> : null}
        {/* Card 1 — out of the flow. Just a helper to open the dedicated Chrome. */}
        <section className="setup-helper">
          <header>
            <h3>专用 Chrome 窗口</h3>
            <p>所有抓取、登录、OpenCLI Bridge 都在这里完成。点击启动,会打开一个隔离的 Chrome。</p>
          </header>
          <Button variant="primary" onClick={launchChrome}>
            启动专用 Chrome
          </Button>
          {chromeError && <p className="setup-card-error">{chromeError}</p>}
          {browserConsoleUrl ? (
            <div className="setup-browser-console">
              <div>
                <strong>容器浏览器已启动</strong>
                <Button type="button" variant="ghost" size="sm" onClick={() => dispatch({ browserConsoleUrl: null })}>
                  收起
                </Button>
              </div>
              <BrowserConsoleFrame
                url={browserConsoleUrl}
                compact
                onExpired={() => dispatch({ browserConsoleUrl: null })}
              />
            </div>
          ) : null}
        </section>

        {/* Card 2 — bind the OpenCLI contextId we just installed. */}
        <SetupCard
          step={1}
          title="连接 OpenCLI Bridge 扩展"
          status={extensionBusy ? "in_progress" : state.extension.loaded && boundContextId ? "done" : "pending"}
          primaryLabel={extensionBusy ? "正在扫描连接..." : "检查连接"}
          onPrimary={verifyExtension}
          details={
            <>
              <p>
                Docker 镜像已固定并预装{" "}
                <a
                  href="https://chromewebstore.google.com/detail/opencli/ildkmabpimmkaediidaifkhjpohdnifk"
                  target="_blank"
                  rel="noreferrer"
                >
                  OpenCLI Bridge
                </a>
                。首次启动后点击检查连接；验证时可能安全重启专用 Chrome 以确认绑定，登录 Cookie 和专用 Profile 会保留。
              </p>
              {boundContextId && <p className="hint">已绑定 contextId: {boundContextId}</p>}
              {extensionError && <p className="setup-card-error">{extensionError}</p>}
            </>
          }
        />

        {/* Card 3 — verify each platform directly via opencli. */}
        <SetupCard
          step={2}
          title="登录平台账号"
          status={
            platforms.some((definition) => state.accounts[definition.platform]?.persisted === true) ? "done" : "pending"
          }
          details={
            <ul className="platform-list">
              {platforms.map((definition) => {
                const p = definition.platform;
                const account = state.accounts[p];
                const status = account?.status ?? "unknown";
                const isBusy = platformBusy[p] === true;
                const error = persistenceError(account);
                return (
                  <li key={p} data-status={status}>
                    <span className="platform-list__name">{definition.label}</span>
                    <span className="platform-list__status">登录状态：{loginStatusLabel(status)}</span>
                    <span className="platform-list__status">
                      持久化状态：
                      {account?.persisted === true ? "已验证" : "未验证"}
                    </span>
                    <span className="platform-list__status">
                      验证阶段：
                      {persistenceStageLabel(account?.persistence_stage ?? null)}
                    </span>
                    {error && <span className="platform-list__error">持久化错误：{error}</span>}
                    <Button variant="ghost" size="sm" onClick={() => probePlatform(p)} disabled={isBusy}>
                      {isBusy ? "正在验证登录持久化..." : "验证登录持久化"}
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => skipPlatform(p)} disabled={isBusy}>
                      跳过
                    </Button>
                  </li>
                );
              })}
            </ul>
          }
        />
        <p className="hint">
          此主动操作会安全重启一次 InfoOS 专用 Chrome，以确认登录状态可持久化；不会读取或展示 Cookie 值。
        </p>

        {/* Card 4 — run the first fetch. */}
        <SetupCard
          step={3}
          title="跑一次测试抓取"
          status={state.first_fetch.ok === true ? "done" : state.first_fetch.ok === false ? "failed" : "pending"}
          primaryLabel="跑一次试试"
          onPrimary={runFirstFetch}
          details={
            state.first_fetch.ok === true ? (
              <p className="hint">已抓取 {state.first_fetch.fetched_count} 条</p>
            ) : firstFetchQueued ? (
              <p className="hint">已加入任务队列，后台 worker 完成抓取后将自动完成此步骤。</p>
            ) : state.first_fetch.ok === false ? (
              <p className="setup-card-error">抓取失败,查看 logs。</p>
            ) : undefined
          }
        />

        <div className="onboarding-actions">
          <Button variant="ghost" onClick={dismiss}>
            跳过,稍后设置
          </Button>
        </div>
      </div>
    </Modal>
  );
}
