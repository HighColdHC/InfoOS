import "../../../styles/_collector-environment.css";
import { RefreshCw, Settings2 } from "lucide-react";
import { Button } from "../../ui/primitives/Button";
import { AccountBlockingBanner } from "./collectionEnvironment/AccountBlockingBanner";
import { AccountFilters } from "./collectionEnvironment/AccountFilters";
import { AccountStatusDialog } from "./collectionEnvironment/AccountStatusDialog";
import { ComponentOverview } from "./collectionEnvironment/ComponentOverview";
import { NotificationSettings } from "./collectionEnvironment/NotificationSettings";
import { BrowserRuntimeMode } from "./collectionEnvironment/BrowserRuntimeMode";
import { AuthenticationRecoveryPanel } from "./collectionEnvironment/AuthenticationRecoveryPanel";
import { BrowserConsoleModal } from "../../modals/BrowserConsoleModal";
import { PlatformAccountTable } from "./collectionEnvironment/PlatformAccountTable";
import { RuntimeCircuitProtection } from "./collectionEnvironment/RuntimeCircuitProtection";
import { useCollectionEnvironment } from "./collectionEnvironment/useCollectionEnvironment";

export function CollectionEnvironmentModule({ onStartSetup }: { onStartSetup: () => Promise<void> }) {
  const {
    state,
    dispatch,
    snapshot,
    loading,
    error,
    reload,
    authRecovery,
    authRecoveryLoading,
    recoveryError,
    filteredAccounts,
    selectedAccount,
    accounts,
    startSetup,
    openRecoveryConsole,
    verifyRecovery,
    openPendingRecoveryConsole,
    verifyPendingRecovery,
    openChrome,
    checkAccount,
    login,
  } = useCollectionEnvironment(onStartSetup);
  const {
    query,
    status,
    openingChrome,
    checkingPlatform,
    loginPlatform,
    selectedPlatform,
    dialogError,
    startingSetup,
    browserConsoleUrl,
    browserConsoleDescription,
    openingRecoveryConsole,
    verifyingRecovery,
    openingPendingRecoveryId,
    verifyingPendingRecoveryId,
  } = state;
  return (
    <section className="collector-environment-page" aria-label="采集环境维护工作区">
      <div className="collector-environment-heading">
        <div>
          <h2>环境状态</h2>
          <p>读取持久化快照；只有明确操作才会打开 Chrome 或检查账号。</p>
        </div>
        <div className="collector-environment-heading-actions">
          <Button icon={Settings2} loading={startingSetup} onClick={() => void startSetup()}>
            运行首次设置
          </Button>
          <Button icon={RefreshCw} loading={loading} onClick={() => void reload()}>
            刷新状态
          </Button>
        </div>
      </div>
      {authRecoveryLoading ? (
        <div className="collector-environment-loading" role="status">
          正在读取登录恢复…
        </div>
      ) : null}
      {recoveryError && !authRecovery ? (
        <div className="collector-environment-load-error" role="alert">
          登录恢复读取失败：{recoveryError}
        </div>
      ) : null}
      {authRecovery ? (
        <AuthenticationRecoveryPanel
          incident={authRecovery}
          error={recoveryError}
          opening={openingRecoveryConsole}
          verifying={verifyingRecovery}
          onOpenConsole={() => void openRecoveryConsole()}
          onVerify={() => void verifyRecovery()}
        />
      ) : null}
      <BrowserRuntimeMode />
      <NotificationSettings />
      {error ? (
        <div className="collector-environment-load-error" role="alert">
          环境状态读取失败：{error}
        </div>
      ) : null}
      {loading && !snapshot ? (
        <div className="collector-environment-loading" role="status">
          正在读取环境快照…
        </div>
      ) : null}
      {snapshot ? (
        <>
          {snapshot.runtime_health?.status === "unhealthy" ? (
            <div className="collector-environment-runtime-alert" role="alert">
              <strong>容器运行异常</strong>
              <span>{snapshot.runtime_health.reasons.join("；") || "聚合健康检查未通过"}</span>
              <span>请查看容器日志并修复对应组件，健康恢复前不要提交新的采集任务。</span>
            </div>
          ) : null}
          <RuntimeCircuitProtection
            circuits={snapshot.runtime_circuits}
            recoveries={snapshot.pending_auth_recoveries}
            openingRecoveryId={openingPendingRecoveryId}
            verifyingRecoveryId={verifyingPendingRecoveryId}
            onOpenRecovery={(recovery) => void openPendingRecoveryConsole(recovery)}
            onVerifyRecovery={(recovery) => void verifyPendingRecovery(recovery)}
          />
          <ComponentOverview
            components={snapshot.components}
            openingChrome={openingChrome}
            onOpenChrome={() => void openChrome()}
          />
          <section className="collector-environment-accounts" aria-label="平台账号区">
            <div className="collector-environment-section-title">
              <div>
                <h2>平台账号</h2>
                <p>采集平台与授权辅助账号由服务端提供；检查和登录只作用当前行。</p>
              </div>
              <span>快照 {snapshot.snapshot_at ?? "未知"}</span>
            </div>
            <AccountBlockingBanner accounts={accounts} />
            <AccountFilters
              query={query}
              status={status}
              onQuery={(value) => dispatch({ query: value })}
              onStatus={(value) => dispatch({ status: value })}
            />
            {filteredAccounts.length ? (
              <PlatformAccountTable
                accounts={filteredAccounts}
                checkingPlatform={checkingPlatform}
                loginPlatform={loginPlatform}
                onCheck={(account) => void checkAccount(account)}
                onLogin={(account) => void login(account)}
              />
            ) : (
              <div className="collector-environment-empty">没有符合条件的平台账号</div>
            )}
          </section>
        </>
      ) : null}
      <AccountStatusDialog
        account={selectedAccount}
        checking={checkingPlatform === selectedPlatform}
        loggingIn={loginPlatform === selectedPlatform}
        error={dialogError}
        onClose={() => {
          if (!checkingPlatform && !loginPlatform) {
            dispatch({ selectedPlatform: null, dialogError: null });
          }
        }}
        onLogin={() => {
          if (selectedAccount) void login(selectedAccount);
        }}
      />
      <BrowserConsoleModal
        url={browserConsoleUrl}
        description={browserConsoleDescription}
        onClose={() => {
          dispatch({
            browserConsoleUrl: null,
            browserConsoleDescription: undefined,
          });
        }}
      />
    </section>
  );
}
