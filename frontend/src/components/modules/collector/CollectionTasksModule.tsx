import "../../../styles/_collector-cards.css";
import "../../../styles/_collector-tasks.css";
import "../../../styles/_source-run-pipeline.css";
import { RefreshCw } from "lucide-react";
import { Button } from "../../ui/primitives/Button";
import { SegmentedControl } from "../../ui/primitives/SegmentedControl";
import { RunBlockingBanner } from "../taskManager/sourceRunTrace/RunBlockingBanner";
import { SourceRunFilters } from "../taskManager/sourceRunTrace/SourceRunFilters";
import { SourceRunMetrics } from "../taskManager/sourceRunTrace/SourceRunMetrics";
import { SourceRunTable } from "../taskManager/sourceRunTrace/SourceRunTable";
import { ResearchRunTable } from "../taskManager/sourceRunTrace/ResearchRunTable";
import { SourceRunTraceDialog } from "../taskManager/sourceRunTrace/SourceRunTraceDialog";
import { QueueOverview } from "../taskManager/sourceRunTrace/QueueOverview";
import { GlobalTaskQueueTracking } from "../taskManager/sourceRunTrace/GlobalTaskQueueTracking";
import { ResearchPanel } from "../taskManager/research/ResearchPanel";
import { collectorPlatformOptions } from "../../../lib/platforms";
import { useCollectionTasks, TASK_TABS, TASK_PAGE_SIZE as PAGE_SIZE } from "./collectionTasks/useCollectionTasks";

export function CollectionTasksModule() {
  const {
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
  } = useCollectionTasks();
  const {
    queryDraft,
    query,
    platform,
    status,
    executionScope,
    activeTab,
    timeFrom,
    timeTo,
    offset,
    refresh,
    selected,
    retryingTaskId,
    traceLoadingMore,
  } = state;
  return (
    <section className="collector-task-page" aria-label="采集任务管理工作区">
      <SegmentedControl
        options={TASK_TABS}
        value={activeTab}
        onChange={updateTaskTab}
        label="任务类型"
        tabIdPrefix="collector-task-tab-"
        panelIdPrefix="collector-task-panel-"
      />
      <div className="collector-task-heading">
        <div>
          <h2>{heading.title}</h2>
          <p>{heading.description}</p>
        </div>
        <Button
          icon={RefreshCw}
          onClick={() => dispatch((current) => ({ refresh: current.refresh + 1 }))}
          loading={activeTab !== "queue" && loading}
        >
          刷新当前页
        </Button>
      </div>
      <div
        id={`collector-task-panel-${activeTab}`}
        className="collector-task-tab-panel"
        role="tabpanel"
        aria-labelledby={`collector-task-tab-${activeTab}`}
      >
        {activeTab === "research" ? <ResearchPanel /> : null}
        {activeTab === "collection" && page ? (
          <SourceRunMetrics metrics={page.summary} countsExact={page.counts_exact} />
        ) : null}
        {activeTab === "collection" ? <RunBlockingBanner sources={blockedSources} /> : null}
        {activeTab === "queue" && page ? <QueueOverview queues={page.queue_summary} /> : null}
        {activeTab === "queue" ? <GlobalTaskQueueTracking refreshKey={refresh} /> : null}
        {activeTab !== "queue" ? (
          <div className="collector-task-panel">
            <SourceRunFilters
              query={queryDraft}
              platform={platform}
              platformOptions={collectorPlatformOptions(items.map((item) => item.platform))}
              status={status}
              executionScope={executionScope}
              timeFrom={timeFrom}
              timeTo={timeTo}
              onQuery={(value) => dispatch({ queryDraft: value })}
              onPlatform={(value) => dispatch({ platform: value })}
              onStatus={(value) => dispatch({ status: value })}
              onExecutionScope={updateExecutionScope}
              onTimeFrom={(value) => dispatch({ timeFrom: value })}
              onTimeTo={(value) => dispatch({ timeTo: value })}
              onSubmit={applyFilters}
              onReset={resetFilters}
            />
            {loading && page ? (
              <div className="collector-task-refreshing" role="status">
                正在刷新来源运行…
              </div>
            ) : null}
            {loading && !page ? (
              <div className="collector-task-state" role="status">
                正在读取来源运行…
              </div>
            ) : null}
            {error ? (
              <div className="collector-task-state is-error" role="alert">
                列表读取失败：{error}
              </div>
            ) : null}
            {!loading && !error && page && !items.length ? (
              <div className="collector-task-state">
                没有符合条件的
                {activeTab === "research" ? "调研任务" : "来源运行"}
              </div>
            ) : null}
            {items.length ? (
              activeTab === "research" ? (
                <ResearchRunTable items={items} onTrace={openTrace} />
              ) : (
                <SourceRunTable items={items} onTrace={openTrace} />
              )
            ) : null}
            {page ? (
              <div className="collector-task-pagination">
                <span>
                  {page.total_exact === false ? `已扫描至少 ${page.total} 条` : `共 ${page.total} 条`} · 第{" "}
                  {Math.floor(offset / PAGE_SIZE) + 1} 页
                </span>
                <div>
                  <Button
                    size="sm"
                    disabled={offset === 0 || loading}
                    onClick={() => dispatch({ offset: Math.max(0, offset - PAGE_SIZE) })}
                  >
                    上一页
                  </Button>
                  <Button
                    size="sm"
                    aria-label="下一页"
                    disabled={page.next_offset === null || loading}
                    onClick={() => dispatch({ offset: page.next_offset ?? offset })}
                  >
                    下一页
                  </Button>
                </div>
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
      <SourceRunTraceDialog
        selected={selected}
        trace={trace}
        loading={traceLoading}
        loadingMore={traceLoadingMore}
        error={traceFailure}
        retryingTaskId={retryingTaskId}
        onClose={closeTrace}
        onReload={() => reloadTrace()}
        onLoadMore={loadMoreTrace}
        onCopy={copy}
        onRetry={retryTask}
      />
    </section>
  );
}
