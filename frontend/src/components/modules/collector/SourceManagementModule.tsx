import "../../../styles/_collector-sources.css";
import { Plus, RefreshCw } from "lucide-react";
import { Button } from "../../ui/primitives/Button";
import { SourceMetrics } from "./sourceManagement/SourceMetrics";
import { SourceFilters } from "./sourceManagement/SourceFilters";
import { SourceTable } from "./sourceManagement/SourceTable";
import { SourceCreateEditModal } from "./sourceManagement/SourceCreateEditModal";
import { SourceDetailDialog } from "./sourceManagement/SourceDetailDialog";
import { SourceRunConfirmDialog } from "./sourceManagement/SourceRunConfirmDialog";
import { SourceActivationDialog } from "./sourceManagement/SourceActivationDialog";
import { collectorPlatformOptions } from "../../../lib/platforms";
import { useSourceManagement, SOURCE_PAGE_SIZE as PAGE_SIZE } from "./sourceManagement/useSourceManagement";

export function SourceManagementModule() {
  const {
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
  } = useSourceManagement();
  const {
    queryDraft,
    platform,
    status,
    offset,
    formTarget,
    formBusy,
    formError,
    duplicateSourceId,
    busySourceId,
    activationTarget,
    runTarget,
    runBusy,
    lastSave,
  } = state;
  return (
    <section className="collector-source-page" aria-label="信息源维护工作区">
      {page ? <SourceMetrics summary={page.summary} /> : null}
      <div className="collector-source-heading">
        <div>
          <h2>来源列表</h2>
          <p>维护平台、稳定标识、采集频率与持久化运行状态。</p>
        </div>
        <Button
          variant="primary"
          icon={Plus}
          onClick={() => {
            dispatch({ formError: null, formTarget: null });
          }}
        >
          新增信息源
        </Button>
      </div>
      <div className="collector-source-panel">
        <SourceFilters
          queryDraft={queryDraft}
          platform={platform}
          status={status}
          platformOptions={collectorPlatformOptions(page?.items.map((item) => item.platform))}
          onQueryDraftChange={(value) => dispatch({ queryDraft: value })}
          onSearch={() => resetOffsetAnd(() => dispatch({ query: queryDraft.trim() }))}
          onPlatformChange={(value) => resetOffsetAnd(() => dispatch({ platform: value }))}
          onStatusChange={(value) => resetOffsetAnd(() => dispatch({ status: value }))}
        />
        {loading && !page ? (
          <div className="collector-source-state" role="status">
            正在读取信息源…
          </div>
        ) : null}
        {loading && page ? (
          <div className="collector-source-refreshing" role="status">
            正在刷新来源列表…
          </div>
        ) : null}
        {listError ? (
          <div className="collector-source-state is-error" role="alert">
            <span>信息源列表读取失败：{listError}</span>
            <Button icon={RefreshCw} onClick={() => void reload()}>
              重试
            </Button>
          </div>
        ) : null}
        {!loading && !listError && page && !items.length ? (
          <div className="collector-source-state">没有符合条件的信息源</div>
        ) : null}
        {!listError && items.length ? (
          <SourceTable
            items={items}
            cadenceSuggestions={page?.cadence_suggestions ?? {}}
            busySourceId={busySourceId}
            onAdoptCadence={(source, hours) => void adoptCadence(source, hours)}
            onDetail={(value) => dispatch({ detailSource: value })}
            onEdit={(source) => {
              dispatch({ formError: null, formTarget: source });
            }}
            onToggle={(value) => dispatch({ activationTarget: value })}
            onRun={(value) => dispatch({ runTarget: value })}
          />
        ) : null}
        {page ? (
          <div className="collector-source-pagination">
            <span>
              共 {page.total} 个结果 · 第 {Math.floor(page.offset / page.limit) + 1} 页
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
                disabled={offset + page.limit >= page.total || loading}
                onClick={() => dispatch({ offset: offset + PAGE_SIZE })}
                aria-label="下一页"
              >
                下一页
              </Button>
            </div>
          </div>
        ) : null}
      </div>
      {lastSave ? (
        <div className="collector-source-result" role="status">
          <div>
            <strong>配置已保存 · {lastSave.source_id}</strong>
            <span>
              {lastSave.source.current_config_version
                ? `配置版本 ${lastSave.source.current_config_version}`
                : "配置版本已由服务端记录"}
              {lastSave.source.config_effective_at ? ` · 生效于 ${lastSave.source.config_effective_at}` : ""}
            </span>
            <span>
              {lastSave.initial_fetch ? `已提交 Initial Fetch · ${lastSave.source_run_id}` : "未提交 Initial Fetch"}
            </span>
          </div>
          {lastSave.source_run_id ? (
            <a className="btn btn--secondary btn--sm" href="?menu=tasks">
              前往采集任务
            </a>
          ) : null}
        </div>
      ) : null}
      <SourceCreateEditModal
        source={formTarget}
        busy={formBusy}
        error={formError}
        duplicateSourceId={duplicateSourceId}
        onOpenDuplicate={() => void openDuplicateSource()}
        onClose={() => {
          if (!formBusy) {
            dispatch({ formTarget: undefined, duplicateSourceId: null });
          }
        }}
        onSave={saveSource}
      />
      <SourceDetailDialog
        detail={detail}
        loading={detailLoading}
        error={detailError}
        onClose={closeDetail}
        onEdit={() => {
          const source = detail?.source;
          closeDetail();
          if (source) dispatch({ formTarget: source });
        }}
        onRun={() => {
          const source = detail?.source;
          closeDetail();
          if (source) dispatch({ runTarget: source });
        }}
      />
      <SourceRunConfirmDialog
        source={runTarget}
        busy={runBusy}
        onClose={() => {
          if (!runBusy) dispatch({ runTarget: null });
        }}
        onSubmit={submitRun}
      />
      <SourceActivationDialog
        source={activationTarget}
        busy={Boolean(busySourceId)}
        onClose={() => {
          if (!busySourceId) dispatch({ activationTarget: null });
        }}
        onConfirm={toggleSource}
      />
    </section>
  );
}
