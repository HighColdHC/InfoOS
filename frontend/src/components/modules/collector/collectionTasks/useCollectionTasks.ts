import { errorMessage } from "../../../../lib/format";
import { useCallback, useEffect, useReducer, useRef } from "react";
import { isAbort } from "../../../../lib/isAbort";
import { useResource } from "../../../ui/hooks/useResource";
import { useToast } from "../../../ui/Toast";
import { fetchSourceRuns, fetchSourceRunTrace, retrySourceRunTask } from "../../../../lib/api/sourceRuns";
import type { SourceRunExecutionScope, SourceRunListItem, SourceRunTaskKind } from "../../../../lib/types/sourceRuns";
export const TASK_PAGE_SIZE = 15;
const PAGE_SIZE = TASK_PAGE_SIZE;
export type TaskTab = "collection" | "research" | "queue";
export const TASK_TABS: Array<{ value: TaskTab; label: string }> = [
  { value: "collection", label: "来源采集" },
  { value: "research", label: "调研任务" },
  { value: "queue", label: "任务队列" },
];
function initialTab(): TaskTab {
  const tab = new URLSearchParams(window.location.search).get("tab");
  return TASK_TABS.some((item) => item.value === tab) ? (tab as TaskTab) : "collection";
}

export function useCollectionTasks() {
  const toast = useToast();
  const initialCaptureId = new URLSearchParams(window.location.search).get("capture_id")?.trim() ?? "";
  const initialState = {
    queryDraft: initialCaptureId,
    query: initialCaptureId,
    platform: "",
    status: "",
    executionScope: "all" as SourceRunExecutionScope,
    activeTab: initialTab() as TaskTab,
    taskView: "collection" as SourceRunTaskKind,
    timeFrom: "",
    timeTo: "",
    offset: 0,
    refresh: 0,
    selected: null as SourceRunListItem | null,
    traceError: null as string | null,
    retryingTaskId: null as string | null,
    traceLoadingMore: false,
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
    queryDraft,
    query,
    platform,
    status,
    executionScope,
    activeTab,
    taskView,
    timeFrom,
    timeTo,
    offset,
    refresh,
    selected,
    traceError,
    traceLoadingMore,
  } = state;
  const tracePageRequest = useRef<{
    controller: AbortController;
    runId: string;
  } | null>(null);

  const listResource = useResource(
    async (signal) => {
      const result = await fetchSourceRuns(
        {
          query,
          platform,
          status,
          executionScope,
          taskView,
          timeFrom: timeFrom ? new Date(timeFrom).toISOString() : undefined,
          timeTo: timeTo ? new Date(timeTo).toISOString() : undefined,
          limit: PAGE_SIZE,
          offset,
        },
        signal,
      );
      if (!signal.aborted && selected && !result.items.some((item) => item.source_run_id === selected.source_run_id)) {
        dispatch({ selected: null });
        setTrace(null);
        toast.pushToast({
          kind: "info",
          message: "当前运行已不在刷新结果中，追踪窗口已关闭",
        });
      }
      return result;
    },
    [query, platform, status, executionScope, taskView, timeFrom, timeTo, offset, refresh],
  );
  const { data: page, loading, error, setData: setPage } = listResource;
  const traceResource = useResource(selected ? (signal) => fetchSourceRunTrace(selected.source_run_id, signal) : null, [
    selected?.source_run_id,
  ]);
  const { data: trace, loading: traceLoading, setData: setTrace } = traceResource;
  const traceFailure = traceError ?? traceResource.error;

  useEffect(() => {
    tracePageRequest.current?.controller.abort();
    tracePageRequest.current = null;
    dispatch({ traceLoadingMore: false });
    return () => {
      tracePageRequest.current?.controller.abort();
      tracePageRequest.current = null;
    };
  }, [selected?.source_run_id]);

  const copy = useCallback(
    async (value: string, label: string) => {
      try {
        await navigator.clipboard.writeText(value);
        toast.pushToast({ kind: "success", message: `${label} 已复制` });
      } catch {
        toast.pushToast({ kind: "error", message: `${label} 复制失败` });
      }
    },
    [toast],
  );
  async function retryTask(taskId: string) {
    dispatch({ retryingTaskId: taskId });
    try {
      await retrySourceRunTask(taskId);
      toast.pushToast({
        kind: "success",
        message: `已为 ${taskId} 创建新尝试`,
      });
      reloadTrace();
    } catch (reason) {
      toast.pushToast({
        kind: "error",
        message: `重试失败：${errorMessage(reason)}`,
      });
    } finally {
      dispatch({ retryingTaskId: null });
    }
  }
  async function loadMoreTrace() {
    const cursor = trace?.item_page.next_cursor;
    if (!selected || !trace || !cursor || traceLoadingMore) return;
    const runId = selected.source_run_id;
    const controller = new AbortController();
    tracePageRequest.current?.controller.abort();
    tracePageRequest.current = { controller, runId };
    const isCurrentPageRequest = () => tracePageRequest.current?.controller === controller;
    dispatch({ traceLoadingMore: true, traceError: null });
    try {
      const next = await fetchSourceRunTrace(runId, controller.signal, cursor);
      if (!isCurrentPageRequest()) return;
      setTrace((current) => {
        if (!current || current.run.source_run_id !== runId || next.run.source_run_id !== runId) return current;
        const seen = new Set(current.items.map((item) => item.raw_item_id));
        const appended = next.items.filter((item) => !seen.has(item.raw_item_id));
        const items = [...current.items, ...appended];
        return { ...next, items, item_page: { ...next.item_page, items } };
      });
    } catch (reason) {
      if (!isAbort(reason) && isCurrentPageRequest())
        dispatch({ traceError: `加载更多失败：${errorMessage(reason)}` });
    } finally {
      if (isCurrentPageRequest()) {
        tracePageRequest.current = null;
        dispatch({ traceLoadingMore: false });
      }
    }
  }
  function applyFilters() {
    dispatch({ offset: 0, query: queryDraft.trim() });
  }
  function resetFilters() {
    dispatch({
      queryDraft: "",
      query: "",
      platform: "",
      status: "",
      executionScope: "all",
      timeFrom: "",
      timeTo: "",
      offset: 0,
    });
  }
  function updateExecutionScope(value: SourceRunExecutionScope) {
    dispatch({ executionScope: value, offset: 0 });
  }
  function updateTaskTab(value: TaskTab) {
    dispatch({ activeTab: value });
    const url = new URL(window.location.href);
    url.searchParams.set("tab", value);
    if (value !== "research") url.searchParams.delete("keywords");
    window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}${url.hash}`);
    dispatch({ offset: 0 });
    closeTrace();
    if (value !== "queue" && value !== taskView) {
      setPage(null);
      dispatch({ taskView: value });
    }
  }
  function openTrace(run: SourceRunListItem) {
    tracePageRequest.current?.controller.abort();
    tracePageRequest.current = null;
    dispatch({ traceLoadingMore: false });
    setTrace(null);
    dispatch({ selected: run, traceError: null });
  }
  function closeTrace() {
    tracePageRequest.current?.controller.abort();
    tracePageRequest.current = null;
    dispatch({ traceLoadingMore: false, selected: null });
    setTrace(null);
    dispatch({ traceError: null });
  }
  const items = page?.items ?? [];
  const blockedSources = items
    .filter((item) => item.blocking_reason)
    .map((item) => ({
      source_id: item.source_id,
      source_name: item.source_name,
      platform: item.platform,
      reason: item.blocking_reason!,
    }));
  const heading =
    activeTab === "research"
      ? {
          title: "调研任务",
          description: "按单次调研查看实际检索语料、处理进度和关联信息卡。",
        }
      : activeTab === "queue"
        ? {
            title: "任务队列",
            description: "查看各执行队列的负载，以及来源链、维护、导出等持久任务。",
          }
        : {
            title: "来源采集",
            description: "按来源运行查看从链接发现到信息卡完成的真实持久化证据。",
          };

  function reloadTrace() {
    dispatch({ traceError: null });
    void traceResource.reload();
  }
  return {
    state,
    dispatch,
    page,
    loading,
    error,
    trace,
    traceLoading,
    traceFailure,
    copy,
    retryTask,
    loadMoreTrace,
    applyFilters,
    resetFilters,
    updateExecutionScope,
    updateTaskTab,
    openTrace,
    closeTrace,
    items,
    blockedSources,
    heading,
    reloadTrace,
  };
}
