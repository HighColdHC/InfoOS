import { errorMessage } from "../../../../lib/format";
import { useReducer } from "react";
import { useResource } from "../../../ui/hooks/useResource";
import { useToast } from "../../../ui/Toast";
import {
  createManagedSource,
  fetchSourceDetail,
  fetchSourcePage,
  setManagedSourceEnabled,
  SourceManagementApiError,
  submitManagedSourceRun,
  updateManagedSource,
} from "../../../../lib/api/sourceManagement";
import type {
  SourceConfigurationPayload,
  SourceManagementItem,
  SourceManagementResult,
} from "../../../../lib/types/sourceManagement";
import { toPayload } from "./sourceFormUtils";

export const SOURCE_PAGE_SIZE = 25;
const PAGE_SIZE = SOURCE_PAGE_SIZE;

export function useSourceManagement() {
  const toast = useToast();
  const initialState = {
    queryDraft: "",
    query: "",
    platform: "",
    status: "",
    offset: 0,
    formTarget: undefined as SourceManagementItem | null | undefined,
    formBusy: false,
    formError: null as string | null,
    duplicateSourceId: null as string | null,
    busySourceId: null as string | null,
    activationTarget: null as SourceManagementItem | null,
    runTarget: null as SourceManagementItem | null,
    runBusy: false,
    detailSource: null as SourceManagementItem | null,
    lastSave: null as SourceManagementResult | null,
  };
  type State = typeof initialState;
  const [state, dispatch] = useReducer(
    (current: State, patch: Partial<State> | ((value: State) => Partial<State>)) => ({
      ...current,
      ...(typeof patch === "function" ? patch(current) : patch),
    }),
    initialState,
  );
  const {
    query,
    platform,
    status,
    offset,
    formTarget,
    formBusy,
    duplicateSourceId,
    busySourceId,
    activationTarget,
    runTarget,
    runBusy,
    detailSource,
  } = state;

  const {
    data: page,
    loading,
    error: listError,
    reload,
  } = useResource(
    (signal) => fetchSourcePage({ query, platform, status, limit: PAGE_SIZE, offset }, signal),
    [query, platform, status, offset],
  );
  const detailResource = useResource(
    detailSource ? (signal) => fetchSourceDetail(detailSource.source_id, signal) : null,
    [detailSource],
    { resetOnLoad: true },
  );
  const { data: detail, loading: detailLoading } = detailResource;
  const detailError = detailResource.error ? `来源详情读取失败：${detailResource.error}` : null;

  function resetOffsetAnd(action: () => void) {
    dispatch({ offset: 0 });
    action();
  }

  async function saveSource(payload: SourceConfigurationPayload) {
    if (formBusy) return;
    dispatch({ formBusy: true, formError: null, duplicateSourceId: null });
    try {
      const result = formTarget
        ? await updateManagedSource(formTarget.source_id, payload)
        : await createManagedSource(payload);
      dispatch({ lastSave: result, formTarget: undefined });
      void reload();
      toast.pushToast({
        kind: "success",
        message: `信息源已保存：${result.source_id}`,
      });
      toast.pushToast({
        kind: result.source_run_id ? "success" : "info",
        message: result.source_run_id
          ? `Initial Fetch：${result.source_run_id}`
          : "配置已生效，本次未触发 Initial Fetch",
      });
    } catch (error) {
      const duplicateId =
        error instanceof SourceManagementApiError && error.code === "duplicate_source"
          ? (error.existingSourceId ?? null)
          : null;
      dispatch({ duplicateSourceId: duplicateId });
      const message = duplicateId
        ? `信息源已存在：${duplicateId}。请打开既有来源继续编辑。`
        : `保存失败：${errorMessage(error)}`;
      dispatch({ formError: message });
      toast.pushToast({ kind: "error", message });
    } finally {
      dispatch({ formBusy: false });
    }
  }

  async function openDuplicateSource() {
    if (!duplicateSourceId || formBusy) return;
    dispatch({ formBusy: true });
    try {
      const existing = await fetchSourceDetail(duplicateSourceId);
      dispatch({
        formTarget: undefined,
        duplicateSourceId: null,
        detailSource: existing.source,
      });
    } catch (error) {
      dispatch({ formError: `既有来源读取失败：${errorMessage(error)}` });
    } finally {
      dispatch({ formBusy: false });
    }
  }

  async function toggleSource() {
    const source = activationTarget;
    if (!source) return;
    const enabled = !source.enabled;
    dispatch({ busySourceId: source.source_id });
    try {
      await setManagedSourceEnabled(source.source_id, enabled);
      toast.pushToast({
        kind: "success",
        message: `${source.display_name} 已${enabled ? "启用" : "停用"}`,
      });
      void reload();
    } catch (error) {
      toast.pushToast({
        kind: "error",
        message: `启停失败：${errorMessage(error)}`,
      });
    } finally {
      dispatch({ busySourceId: null, activationTarget: null });
    }
  }

  async function submitRun() {
    if (!runTarget || runBusy || ["blocked", "login_required"].includes(runTarget.readiness_status ?? "")) return;
    dispatch({ runBusy: true });
    try {
      const result = await submitManagedSourceRun(runTarget.source_id);
      toast.pushToast({
        kind: "success",
        message: `采集任务已提交：${result.task_id}`,
      });
      dispatch({ runTarget: null });
      void reload();
    } catch (error) {
      toast.pushToast({
        kind: "error",
        message: `运行提交失败：${errorMessage(error)}`,
      });
    } finally {
      dispatch({ runBusy: false });
    }
  }

  async function adoptCadence(source: SourceManagementItem, hours: number) {
    if (busySourceId || source.cadence_hours === hours) return;
    dispatch({ busySourceId: source.source_id });
    try {
      await updateManagedSource(source.source_id, {
        ...toPayload(source),
        cadence_hours: hours,
      });
      toast.pushToast({
        kind: "success",
        message: `${source.display_name} 已采用每 ${hours} 小时采集`,
      });
      void reload();
    } catch (error) {
      toast.pushToast({
        kind: "error",
        message: `采用建议失败：${errorMessage(error)}`,
      });
    } finally {
      dispatch({ busySourceId: null });
    }
  }

  function closeDetail() {
    dispatch({ detailSource: null });
    detailResource.setData(null);
  }
  const items = page?.items ?? [];

  return {
    state,
    dispatch,
    page,
    loading,
    listError,
    detail,
    detailLoading,
    detailError,
    reload,
    resetOffsetAnd,
    saveSource,
    openDuplicateSource,
    toggleSource,
    submitRun,
    adoptCadence,
    closeDetail,
    items,
  };
}
