import { useReducer } from "react";
import type { SetupState } from "../../../lib/types";
import {
  loadSetupState,
  postSetupDismiss,
  postSetupFirstFetch,
  postSetupLaunch,
  postSetupSkipPlatform,
  postSetupVerifyExtension,
  postSetupVerifyPlatform,
} from "../../../lib/api";
import { markOnboardingComplete } from "../../../lib/onboarding";
import { errorMessage } from "../../../lib/format";
type Platform = string;

export function useOnboardingSetup(state: SetupState, setState: (s: SetupState) => void, onClose: () => void) {
  const initialState = {
    chromeError: null as string | null,
    actionError: null as string | null,
    extensionBusy: false,
    extensionError: null as string | null,
    firstFetchQueued: false,
    boundContextId: state.session.context_id,
    platformBusy: {} as Record<string, boolean>,
    browserConsoleUrl: null as string | null,
  };
  type State = typeof initialState;
  const [operation, dispatch] = useReducer(
    (current: State, patch: Partial<State> | ((value: State) => Partial<State>)) => ({
      ...current,
      ...(typeof patch === "function" ? patch(current) : patch),
    }),
    initialState,
  );

  const dismiss = async () => {
    try {
      await postSetupDismiss();
    } catch (error) {
      // Dismissal remains optional: a network failure must not trap the user in setup.
      console.error("[OnboardingModal] dismiss was not persisted; closing locally", error);
    }
    markOnboardingComplete();
    onClose();
  };

  const launchChrome = async () => {
    dispatch({ chromeError: null });
    try {
      const result = await postSetupLaunch();
      if (!result.ok) {
        dispatch({ chromeError: result.error || "启动失败" });
      } else {
        dispatch({
          browserConsoleUrl: result.console_url,
        });
      }
    } catch (e) {
      dispatch({ chromeError: errorMessage(e) });
    }
  };

  const verifyExtension = async () => {
    dispatch({ extensionBusy: true, extensionError: null });
    try {
      const result = await postSetupVerifyExtension();
      if (result.ok && result.status === "bound") {
        dispatch({ boundContextId: result.context_id ?? null });
        const fresh = await loadSetupState();
        setState(fresh);
      } else {
        // status ∈ {no-connection, opencli-missing, no-extension}
        dispatch({ extensionError: result.hint || "未检测到 OpenCLI 连接" });
      }
    } catch (e) {
      dispatch({ extensionError: errorMessage(e) });
    } finally {
      dispatch({ extensionBusy: false });
    }
  };

  const probePlatform = async (platform: Platform) => {
    dispatch((current) => ({
      actionError: null,
      platformBusy: { ...current.platformBusy, [platform]: true },
    }));
    try {
      await postSetupVerifyPlatform(platform);
      setState(await loadSetupState());
    } catch (error) {
      dispatch({ actionError: `平台验证失败：${errorMessage(error)}` });
    } finally {
      dispatch((current) => ({
        platformBusy: { ...current.platformBusy, [platform]: false },
      }));
    }
  };

  const skipPlatform = async (platform: Platform) => {
    dispatch({ actionError: null });
    try {
      await postSetupSkipPlatform(platform);
      setState(await loadSetupState());
    } catch (error) {
      dispatch({ actionError: `跳过平台失败：${errorMessage(error)}` });
    }
  };

  const runFirstFetch = async () => {
    dispatch({ actionError: null });
    try {
      const result = await postSetupFirstFetch();
      dispatch({ firstFetchQueued: result.queued === true });
      const fresh = await loadSetupState();
      setState(fresh);
      if (fresh.current_step === "complete") {
        markOnboardingComplete();
        onClose();
      }
    } catch (error) {
      dispatch({ actionError: `测试抓取失败：${errorMessage(error)}` });
    }
  };

  return {
    state: operation,
    dispatch,
    dismiss,
    launchChrome,
    verifyExtension,
    probePlatform,
    skipPlatform,
    runFirstFetch,
  };
}
