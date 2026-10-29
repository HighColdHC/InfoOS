import { errorMessage } from "../../../../lib/format";
import { useEffect, useReducer } from "react";
import { useResource } from "../../../ui/hooks/useResource";
import { useToast } from "../../../ui/Toast";
import { fetchExportJob, fetchLogDetail, fetchOperationsLogs, submitExport } from "../../../../lib/api/exportLogs";
import { fetchSourceRuns } from "../../../../lib/api/sourceRuns";
import type { ExportJobDetail, ExportKind, ExportRequest, OperationLog } from "../../../../lib/types/exportLogs";
import { redactedSummary } from "./LogDetailDialog";
import type { LogFilterDraft } from "./LogFilters";
import { updateTaskUrl } from "./exportLogsViewModel";
export const LOG_PAGE_SIZE = 25;
export const EMPTY_FILTERS: LogFilterDraft = {
  query: "",
  level: "",
  area: "",
  timeFrom: "",
  timeTo: "",
};
const PAGE_SIZE = LOG_PAGE_SIZE;
const terminal = (status: string) => ["completed", "failed", "cancelled"].includes(status);

export function useExportLogs() {
  const toast = useToast();
  const initialState = {
    loadingMoreSourceRuns: false,
    preset: null as ExportKind | null,
    submitting: false,
    submitError: null as string | null,
    job: null as ExportJobDetail | null,
    jobLoading: false,
    jobError: null as string | null,
    draft: EMPTY_FILTERS as LogFilterDraft,
    filters: EMPTY_FILTERS as LogFilterDraft,
    logOffset: 0,
    detailId: null as string | null,
  };
  type State = typeof initialState;
  const [state, dispatch] = useReducer(
    (current: State, patch: Partial<State> | ((value: State) => Partial<State>)) => ({
      ...current,
      ...(typeof patch === "function" ? patch(current) : patch),
    }),
    initialState,
  );
  const { loadingMoreSourceRuns, preset, job, filters, logOffset, detailId } = state;

  useEffect(() => {
    const taskId = new URLSearchParams(window.location.search).get("export_task_id");
    if (!taskId) return;
    let current = true;
    dispatch({ jobLoading: true });
    void fetchExportJob(taskId)
      .then((result) => {
        if (current) dispatch({ job: result });
      })
      .catch((error) => {
        if (current) dispatch({ jobError: `导出任务恢复失败：${errorMessage(error)}` });
      })
      .finally(() => {
        if (current) dispatch({ jobLoading: false });
      });
    return () => {
      current = false;
    };
  }, []);

  useEffect(() => {
    if (!job || terminal(job.status)) return;
    let current = true;
    const refreshJob = () =>
      void fetchExportJob(job.task_id)
        .then((result) => {
          if (current) dispatch({ job: result });
        })
        .catch((error) => {
          if (current) dispatch({ jobError: `进度读取失败：${errorMessage(error)}` });
        });
    refreshJob();
    const timer = window.setInterval(refreshJob, 1500);
    return () => {
      current = false;
      window.clearInterval(timer);
    };
  }, [job?.task_id, job?.status]);

  const logsResource = useResource(
    (signal) =>
      fetchOperationsLogs(
        {
          query: filters.query,
          level: filters.level,
          area: filters.area,
          timeFrom: filters.timeFrom || undefined,
          timeTo: filters.timeTo || undefined,
          limit: PAGE_SIZE,
          offset: logOffset,
        },
        signal,
      ),
    [filters, logOffset],
  );
  const { data: logs, loading: logsLoading, error: logsError } = logsResource;
  const detailResource = useResource(detailId ? (signal) => fetchLogDetail(detailId, signal) : null, [detailId], {
    resetOnLoad: true,
  });
  const { data: detail, loading: detailLoading, error: detailError } = detailResource;
  const candidates = useResource(
    preset === "diagnostic"
      ? (signal) =>
          fetchSourceRuns({ limit: 25, offset: 0 }, signal)
      : null,
    [preset],
  );
  const sourceRuns = candidates.data?.items ?? [];
  const sourceRunNextOffset = candidates.data?.next_offset ?? null;
  const sourceRunsLoading = candidates.loading || loadingMoreSourceRuns;

  function openPreset(kind: ExportKind) {
    dispatch({ submitError: null, preset: kind });
  }
  async function createExport(request: ExportRequest) {
    dispatch({ submitting: true, submitError: null });
    try {
      const result = await submitExport(request);
      const next: ExportJobDetail = {
        task_id: result.task_id,
        export_id: result.export_id,
        kind: request.kind,
        status: result.status,
        progress_percent: 0,
        error: null,
        request,
        artifact: null,
      };
      dispatch({ preset: null, job: next });
      updateTaskUrl(result.task_id);
      toast.pushToast({
        kind: "success",
        message: `异步导出任务已创建：${result.task_id}`,
      });
    } catch (error) {
      dispatch({ submitError: `提交失败：${errorMessage(error)}` });
    } finally {
      dispatch({ submitting: false });
    }
  }
  async function loadMoreSourceRuns() {
    if (sourceRunNextOffset === null || sourceRunsLoading) return;
    dispatch({ loadingMoreSourceRuns: true });
    try {
      const result = await fetchSourceRuns({
        limit: 25,
        offset: sourceRunNextOffset,
      });
      candidates.setData((current) => ({
        ...result,
        items: [
          ...(current?.items ?? []),
          ...result.items.filter(
            (item) => !current?.items.some((existing) => existing.source_run_id === item.source_run_id),
          ),
        ],
      }));
    } catch (error) {
      toast.pushToast({
        kind: "error",
        message: `Source Run 候选读取失败：${errorMessage(error)}`,
      });
    } finally {
      dispatch({ loadingMoreSourceRuns: false });
    }
  }
  async function copyLog(log: OperationLog) {
    try {
      await navigator.clipboard.writeText(redactedSummary(log));
      toast.pushToast({ kind: "success", message: "脱敏日志摘要已复制" });
    } catch {
      toast.pushToast({ kind: "error", message: "复制失败" });
    }
  }
  function closeJob() {
    dispatch({ job: null, jobError: null, jobLoading: false });
    updateTaskUrl(null);
  }
  function closeDetail() {
    dispatch({ detailId: null });
    detailResource.setData(null);
  }
  const items = logs?.items ?? [];

  return {
    state,
    dispatch,
    logs,
    logsLoading,
    logsError,
    detail,
    detailLoading,
    detailError,
    sourceRuns,
    sourceRunNextOffset,
    sourceRunsLoading,
    sourceRunsError: candidates.error,
    items,
    openPreset,
    createExport,
    loadMoreSourceRuns,
    copyLog,
    closeJob,
    closeDetail,
  };
}
