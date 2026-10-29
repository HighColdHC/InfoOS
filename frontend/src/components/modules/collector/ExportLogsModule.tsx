import "../../../styles/_collector-export-logs.css";
import { Download, HardDrive } from "lucide-react";
import { Button } from "../../ui/primitives/Button";
import { ExportCapabilityCards } from "./exportLogs/ExportCapabilityCards";
import { ExportDialog } from "./exportLogs/ExportDialog";
import { ExportJobDialog } from "./exportLogs/ExportJobDialog";
import { LogDetailDialog } from "./exportLogs/LogDetailDialog";
import { LogFilters } from "./exportLogs/LogFilters";
import { LogTable } from "./exportLogs/LogTable";
import { useExportLogs, EMPTY_FILTERS, LOG_PAGE_SIZE as PAGE_SIZE } from "./exportLogs/useExportLogs";

export function ExportLogsModule() {
  const {
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
    sourceRunsError,
    items,
    openPreset,
    createExport,
    loadMoreSourceRuns,
    copyLog,
    closeJob,
    closeDetail,
  } = useExportLogs();
  const { preset, submitting, submitError, job, jobLoading, jobError, draft, logOffset } = state;
  return (
    <section className="collector-export-page" aria-label="数据导出与日志工作区">
      <div className="collector-export-heading">
        <div>
          <h2>导出与备份</h2>
          <p>异步生成数据交换文件、本地备份和脱敏诊断包。</p>
        </div>
        <div>
          <Button icon={Download} onClick={() => openPreset("data")}>
            导出数据
          </Button>
          <Button icon={HardDrive} variant="primary" onClick={() => openPreset("backup")}>
            立即备份
          </Button>
        </div>
      </div>
      <ExportCapabilityCards onOpen={openPreset} />
      <section className="collector-logs-panel">
        <div className="collector-logs-title">
          <div>
            <h2>结构化日志</h2>
            <p>所有查询由服务端分页执行，详情和复制只使用脱敏字段。</p>
          </div>
          {logs ? <span>共 {logs.total} 条</span> : null}
        </div>
        <LogFilters
          value={draft}
          onChange={(value) => dispatch({ draft: value })}
          onSubmit={() => {
            dispatch({ logOffset: 0, filters: { ...draft } });
          }}
          onReset={() => {
            dispatch({
              draft: EMPTY_FILTERS,
              filters: EMPTY_FILTERS,
              logOffset: 0,
            });
          }}
        />
        {logsLoading && !logs ? (
          <div className="collector-export-state" role="status">
            正在读取日志…
          </div>
        ) : null}
        {logsError ? (
          <div className="collector-export-state is-error" role="alert">
            日志读取失败：{logsError}
          </div>
        ) : null}
        {!logsLoading && !logsError && logs && !items.length ? (
          <div className="collector-export-state">没有符合条件的日志</div>
        ) : null}
        {items.length ? (
          <LogTable
            items={items}
            onDetail={(item) => dispatch({ detailId: item.log_id })}
            onCopy={(item) => void copyLog(item)}
          />
        ) : null}
        {logs ? (
          <div className="collector-log-pagination">
            <span>第 {Math.floor(logOffset / PAGE_SIZE) + 1} 页</span>
            <div>
              <Button
                size="sm"
                disabled={logOffset === 0 || logsLoading}
                onClick={() => dispatch({ logOffset: Math.max(0, logOffset - PAGE_SIZE) })}
              >
                上一页
              </Button>
              <Button
                size="sm"
                aria-label="下一页日志"
                disabled={logs.next_offset === null || logsLoading}
                onClick={() => dispatch({ logOffset: logs.next_offset ?? logOffset })}
              >
                下一页
              </Button>
            </div>
          </div>
        ) : null}
      </section>
      <ExportDialog
        kind={preset}
        sourceRuns={sourceRuns}
        sourceRunsLoading={sourceRunsLoading}
        sourceRunsError={sourceRunsError}
        sourceRunsHasMore={sourceRunNextOffset !== null}
        submitting={submitting}
        submitError={submitError}
        onLoadMoreSourceRuns={() => void loadMoreSourceRuns()}
        onClose={() => {
          if (!submitting) dispatch({ preset: null });
        }}
        onSubmit={(request) => void createExport(request)}
      />
      <ExportJobDialog job={job} loading={jobLoading} error={jobError} onClose={closeJob} />
      <LogDetailDialog
        log={detail}
        loading={detailLoading}
        error={detailError}
        onClose={closeDetail}
        onCopy={(log) => void copyLog(log)}
      />
    </section>
  );
}
