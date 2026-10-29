import { errorMessage } from "../../../../lib/format";
import { useMemo, useReducer } from "react";
import { useResource } from "../../../ui/hooks/useResource";
import { useToast } from "../../../ui/Toast";
import {
  checkPlatformAccount,
  fetchCollectionEnvironment,
  launchDedicatedChrome,
  launchPlatformLogin,
} from "../../../../lib/api/collectionEnvironment";
import {
  fetchAuthRecovery,
  openAuthRecoveryConsole,
  openPendingAuthRecoveryConsole,
  verifyAuthRecovery,
  verifyPendingAuthRecovery,
} from "../../../../lib/api/authRecovery";
import type { AuthRecoverySummary, PlatformAccount } from "../../../../lib/types/collectionEnvironment";

export function useCollectionEnvironment(onStartSetup: () => Promise<void>) {
  const toast = useToast();
  const initialState = {
    query: "",
    status: "all",
    openingChrome: false,
    checkingPlatform: null as string | null,
    loginPlatform: null as string | null,
    selectedPlatform: null as string | null,
    dialogError: null as string | null,
    startingSetup: false,
    browserConsoleUrl: null as string | null,
    browserConsoleDescription: undefined as string | undefined,
    authRecoveryError: null as string | null,
    openingRecoveryConsole: false,
    verifyingRecovery: false,
    openingPendingRecoveryId: null as string | null,
    verifyingPendingRecoveryId: null as string | null,
  };
  type State = typeof initialState;
  const [state, dispatch] = useReducer(
    (current: State, patch: Partial<State> | ((value: State) => Partial<State>)) => ({
      ...current,
      ...(typeof patch === "function" ? patch(current) : patch),
    }),
    initialState,
  );
  const { query, status, selectedPlatform, authRecoveryError } = state;
  const authRecoveryId = useMemo(() => new URLSearchParams(window.location.search).get("auth_recovery"), []);

  const { data: snapshot, loading, error, reload, setData: setSnapshot } = useResource(fetchCollectionEnvironment, []);
  const recoveryResource = useResource(
    authRecoveryId ? async (signal) => (await fetchAuthRecovery(authRecoveryId, signal)).incident : null,
    [authRecoveryId],
  );
  const { data: authRecovery, loading: authRecoveryLoading, setData: setAuthRecovery } = recoveryResource;
  const recoveryError = authRecoveryError ?? recoveryResource.error;

  async function readPersistedSnapshot() {
    const result = await fetchCollectionEnvironment();
    setSnapshot(result);
    return result;
  }

  async function openChrome() {
    dispatch({ openingChrome: true });
    try {
      const result = await launchDedicatedChrome();
      dispatch({
        browserConsoleUrl: result.console_url,
      });
      toast.pushToast({ kind: "success", message: "容器内专用 Chrome 已打开" });
    } catch (reason) {
      toast.pushToast({
        kind: "error",
        message: `专用 Chrome 打开失败：${errorMessage(reason)}`,
      });
    } finally {
      dispatch({ openingChrome: false });
    }
  }

  async function openRecoveryConsole() {
    if (!authRecovery) return;
    dispatch({ openingRecoveryConsole: true, authRecoveryError: null });
    try {
      const result = await openAuthRecoveryConsole(authRecovery.incident_id);
      setAuthRecovery(result.incident);
      dispatch({
        browserConsoleDescription: "请在容器内完成扫码、密码、MFA 或二次确认；完成后关闭控制台并点击完成并验证。",
        browserConsoleUrl: result.console_url,
      });
    } catch (reason) {
      dispatch({ authRecoveryError: errorMessage(reason) });
    } finally {
      dispatch({ openingRecoveryConsole: false });
    }
  }

  async function verifyRecovery() {
    if (!authRecovery) return;
    dispatch({ verifyingRecovery: true, authRecoveryError: null });
    try {
      const result = await verifyAuthRecovery(authRecovery.incident_id);
      setAuthRecovery(result.incident);
      if (!result.ok || result.verification.status !== "passed") {
        dispatch({ authRecoveryError: "登录或安全验证尚未完成" });
        return;
      }
      try {
        await readPersistedSnapshot();
      } catch (reason) {
        dispatch({
          authRecoveryError: `环境快照刷新失败：${errorMessage(reason)}`,
        });
      }
    } catch (reason) {
      dispatch({ authRecoveryError: errorMessage(reason) });
    } finally {
      dispatch({ verifyingRecovery: false });
    }
  }

  async function openPendingRecoveryConsole(recovery: AuthRecoverySummary) {
    dispatch({ openingPendingRecoveryId: recovery.incident_id });
    try {
      const result = await openPendingAuthRecoveryConsole(recovery.open_console_url);
      dispatch({
        browserConsoleDescription: "请在容器内完成扫码、密码、MFA 或二次确认；完成后关闭控制台并点击完成并验证。",
        browserConsoleUrl: result.console_url,
      });
    } catch (reason) {
      toast.pushToast({
        kind: "error",
        message: `打开授权面板失败：${errorMessage(reason)}`,
      });
    } finally {
      dispatch({ openingPendingRecoveryId: null });
    }
  }

  async function verifyPendingRecovery(recovery: AuthRecoverySummary) {
    dispatch({ verifyingPendingRecoveryId: recovery.incident_id });
    try {
      const result = await verifyPendingAuthRecovery(recovery.verify_url);
      if (!result.ok || result.verification.status !== "passed") {
        toast.pushToast({ kind: "error", message: "登录或安全验证尚未完成" });
      }
    } catch (reason) {
      toast.pushToast({
        kind: "error",
        message: `登录恢复验证失败：${errorMessage(reason)}`,
      });
    } finally {
      try {
        await readPersistedSnapshot();
      } catch (reason) {
        toast.pushToast({
          kind: "error",
          message: `环境快照刷新失败：${errorMessage(reason)}`,
        });
      }
      dispatch({ verifyingPendingRecoveryId: null });
    }
  }

  async function checkAccount(account: PlatformAccount) {
    dispatch({
      selectedPlatform: account.platform,
      dialogError: null,
      checkingPlatform: account.platform,
    });
    try {
      await checkPlatformAccount(account.platform);
      toast.pushToast({
        kind: "success",
        message: `${account.label} 状态检查完成`,
      });
    } catch (reason) {
      const message = `状态检查失败：${errorMessage(reason)}`;
      dispatch({ dialogError: message });
      toast.pushToast({ kind: "error", message });
    }
    try {
      await readPersistedSnapshot();
    } catch (reason) {
      dispatch((current) => ({
        dialogError: current.dialogError ?? `状态刷新失败：${errorMessage(reason)}`,
      }));
    } finally {
      dispatch({ checkingPlatform: null });
    }
  }

  async function login(account: PlatformAccount) {
    dispatch({ loginPlatform: account.platform, dialogError: null });
    try {
      const result = await launchPlatformLogin(account.platform);
      dispatch({
        selectedPlatform: null,
        browserConsoleUrl: result.console_url,
      });
      toast.pushToast({
        kind: "success",
        message: `${account.label} 登录页已在容器 Chrome 打开；完成后请再次状态检查`,
      });
    } catch (reason) {
      const message = `登录页打开失败：${errorMessage(reason)}`;
      dispatch({ dialogError: message });
      toast.pushToast({ kind: "error", message });
    } finally {
      dispatch({ loginPlatform: null });
    }
  }

  async function startSetup() {
    dispatch({ startingSetup: true });
    try {
      await onStartSetup();
    } catch (reason) {
      toast.pushToast({
        kind: "error",
        message: `首次设置启动失败：${errorMessage(reason)}`,
      });
    } finally {
      dispatch({ startingSetup: false });
    }
  }

  const accounts = snapshot?.accounts ?? [];
  const filteredAccounts = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    return accounts.filter(
      (account) =>
        (status === "all" || account.status === status) &&
        (!normalized ||
          `${account.label} ${account.platform} ${account.session_id ?? ""}`.toLocaleLowerCase().includes(normalized)),
    );
  }, [accounts, query, status]);
  const selectedAccount = accounts.find((account) => account.platform === selectedPlatform) ?? null;

  return {
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
  };
}
