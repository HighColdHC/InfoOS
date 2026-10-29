import { Button } from "../../../ui/primitives/Button";
import { useEffect, useState } from "react";
import { Modal } from "../../../ui/Modal";
import { estimateExport } from "../../../../lib/api/exportLogs";
import type { ExportEstimate, ExportKind, ExportRequest } from "../../../../lib/types/exportLogs";
import type { SourceRunListItem } from "../../../../lib/types/sourceRuns";
import { exportPreset, exportRequestForTransport, formatBytes } from "./exportLogsViewModel";

type Props = {
  kind: ExportKind | null;
  sourceRuns: SourceRunListItem[];
  sourceRunsLoading: boolean;
  sourceRunsError: string | null;
  sourceRunsHasMore: boolean;
  submitting: boolean;
  submitError: string | null;
  onLoadMoreSourceRuns: () => void;
  onClose: () => void;
  onSubmit: (request: ExportRequest) => void;
};
const titles: Record<ExportKind, string> = {
  data: "导出来源与卡片数据",
  backup: "创建完整本地备份",
  diagnostic: "生成脱敏诊断包",
};

export function ExportDialog({
  kind,
  sourceRuns,
  sourceRunsLoading,
  sourceRunsError,
  sourceRunsHasMore,
  submitting,
  submitError,
  onLoadMoreSourceRuns,
  onClose,
  onSubmit,
}: Props) {
  const [request, setRequest] = useState<ExportRequest>(() => exportPreset("data"));
  const [estimate, setEstimate] = useState<ExportEstimate | null>(null);
  const [estimating, setEstimating] = useState(false);
  const [estimateError, setEstimateError] = useState<string | null>(null);
  useEffect(() => {
    if (kind) setRequest(exportPreset(kind));
  }, [kind]);
  useEffect(() => {
    if (!kind) return;
    let current = true;
    const timer = window.setTimeout(() => {
      setEstimating(true);
      setEstimateError(null);
      void estimateExport(exportRequestForTransport(request))
        .then((result) => {
          if (current) setEstimate(result);
        })
        .catch(() => {
          if (current) {
            setEstimate(null);
            setEstimateError("暂不可估算");
          }
        })
        .finally(() => {
          if (current) setEstimating(false);
        });
    }, 300);
    return () => {
      current = false;
      window.clearTimeout(timer);
    };
  }, [kind, request]);
  function patch(values: Partial<ExportRequest>) {
    setRequest((current) => ({ ...current, ...values }));
  }
  function checkbox(key: keyof ExportRequest, checked: boolean) {
    patch({ [key]: checked } as Partial<ExportRequest>);
  }
  function toggleRun(id: string, checked: boolean) {
    patch({
      source_run_ids: checked
        ? [...request.source_run_ids, id]
        : request.source_run_ids.filter((value) => value !== id),
    });
  }

  return (
    <Modal
      open={Boolean(kind)}
      onClose={onClose}
      title={kind ? titles[kind] : "配置导出"}
      ariaLabel="配置导出范围"
      width={780}
      busy={submitting}
      actions={
        <>
          <Button variant="ghost" size="md" type="button" disabled={submitting} onClick={onClose}>
            取消
          </Button>
          <Button
            variant="primary"
            size="md"
            type="button"
            disabled={submitting}
            onClick={() => onSubmit(exportRequestForTransport(request))}
          >
            {submitting ? "正在提交…" : "创建异步任务"}
          </Button>
        </>
      }
    >
      <div className="collector-export-form">
        {submitError ? <div role="alert">{submitError}</div> : null}
        <div className="collector-export-form-grid">
          <label>
            <span>开始时间</span>
            <input
              type="datetime-local"
              value={request.time_from ?? ""}
              onChange={(event) => patch({ time_from: event.target.value || null })}
            />
          </label>
          <label>
            <span>结束时间</span>
            <input
              type="datetime-local"
              value={request.time_to ?? ""}
              onChange={(event) => patch({ time_to: event.target.value || null })}
            />
          </label>
          <label>
            <span>格式</span>
            <select
              value={request.format}
              disabled={kind !== "data" || request.include_attachment_files}
              onChange={(event) => patch({ format: event.target.value as "json" | "zip" })}
            >
              <option value="json">JSON</option>
              <option value="zip">ZIP</option>
            </select>
          </label>
        </div>
        {kind === "data" ? (
          <fieldset>
            <legend>数据范围</legend>
            <label>
              <input
                type="checkbox"
                checked={request.include_sources_runs}
                onChange={(event) => checkbox("include_sources_runs", event.target.checked)}
              />
              来源与运行
            </label>
            <label>
              <input
                type="checkbox"
                checked={request.include_cards_content}
                onChange={(event) => checkbox("include_cards_content", event.target.checked)}
              />
              信息卡与内容块
            </label>
            <label>
              <input
                type="checkbox"
                checked={request.include_asset_index}
                onChange={(event) => checkbox("include_asset_index", event.target.checked)}
              />
              附件索引
            </label>
            <label>
              <input
                aria-label="包含附件实体文件"
                type="checkbox"
                checked={request.include_attachment_files}
                onChange={(event) =>
                  patch({
                    include_attachment_files: event.target.checked,
                    format: event.target.checked ? "zip" : request.format,
                  })
                }
              />
              附件实体文件
            </label>
            <label>
              <input
                type="checkbox"
                checked={request.include_redacted_logs}
                onChange={(event) => checkbox("include_redacted_logs", event.target.checked)}
              />
              脱敏日志
            </label>
          </fieldset>
        ) : null}
        {kind === "backup" ? (
          <fieldset>
            <legend>备份范围</legend>
            <label>
              <input type="checkbox" checked disabled />
              SQLite 一致性快照
            </label>
            <label>
              <input type="checkbox" checked={request.include_asset_index} disabled />
              附件索引
            </label>
            <label>
              <input
                type="checkbox"
                checked={request.include_media_files}
                onChange={(event) => checkbox("include_media_files", event.target.checked)}
              />
              包含媒体实体文件
            </label>
          </fieldset>
        ) : null}
        {kind === "diagnostic" ? (
          <>
            <fieldset>
              <legend>诊断范围</legend>
              <label>
                <input
                  type="checkbox"
                  checked={request.include_redacted_logs}
                  onChange={(event) => checkbox("include_redacted_logs", event.target.checked)}
                />
                脱敏日志
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={request.include_environment_snapshot}
                  onChange={(event) => checkbox("include_environment_snapshot", event.target.checked)}
                />
                环境状态快照
              </label>
              {["ERROR", "WARNING", "INFO"].map((level) => (
                <label key={level}>
                  <input
                    type="checkbox"
                    checked={request.log_levels.includes(level)}
                    onChange={(event) =>
                      patch({
                        log_levels: event.target.checked
                          ? [...request.log_levels, level]
                          : request.log_levels.filter((value) => value !== level),
                      })
                    }
                  />
                  {level}
                </label>
              ))}
            </fieldset>
            <fieldset>
              <legend>Source Runs</legend>
              {sourceRunsError ? <p role="alert">候选运行读取失败：{sourceRunsError}</p> : null}
              {sourceRuns.map((run) => (
                <label key={run.source_run_id}>
                  <input
                    aria-label={`选择 Source Run ${run.source_run_id}`}
                    type="checkbox"
                    checked={request.source_run_ids.includes(run.source_run_id)}
                    onChange={(event) => toggleRun(run.source_run_id, event.target.checked)}
                  />
                  {run.source_name} · {run.source_run_id}
                </label>
              ))}
              {sourceRunsLoading ? <span role="status">正在读取候选运行…</span> : null}
              {sourceRunsHasMore ? (
                <Button
                  variant="ghost"
                  size="sm"
                  type="button"
                  disabled={sourceRunsLoading}
                  onClick={onLoadMoreSourceRuns}
                >
                  加载更多 Source Runs
                </Button>
              ) : null}
            </fieldset>
          </>
        ) : null}
        <aside className="collector-export-estimate" aria-live="polite">
          {estimating
            ? "估算中…"
            : (estimateError ??
              (estimate
                ? `预计 ${formatBytes(estimate.estimated_bytes)} · ${estimate.file_count} 个文件 · 媒体 ${formatBytes(estimate.media_bytes)}`
                : "等待估算"))}
        </aside>
      </div>
    </Modal>
  );
}
