import { Button } from "../../../ui/primitives/Button";
import { Modal } from "../../../ui/Modal";
import type { ExportJobDetail } from "../../../../lib/types/exportLogs";
import { exportDownloadUrl } from "../../../../lib/api/exportLogs";
import { formatBytes, statusLabel } from "./exportLogsViewModel";

const sectionLabels: Record<string, string> = {
  sources: "来源",
  runs: "来源运行",
  cards: "信息卡",
  assets: "附件索引",
  attachments: "附件文件",
  media: "媒体文件",
  logs: "脱敏日志",
  environment: "环境快照",
};

export function ExportJobDialog({
  job,
  loading,
  error,
  onClose,
}: {
  job: ExportJobDetail | null;
  loading: boolean;
  error: string | null;
  onClose: () => void;
}) {
  return (
    <Modal
      open={Boolean(job || loading || error)}
      onClose={onClose}
      title="导出任务进度"
      ariaLabel="导出任务进度"
      width={680}
      actions={
        <Button variant="secondary" size="md" type="button" aria-label="关闭导出任务" onClick={onClose}>
          关闭
        </Button>
      }
    >
      <div className="collector-export-job">
        {loading && !job ? <div role="status">正在恢复导出任务…</div> : null}
        {error ? <div role="alert">{error}</div> : null}
        {job ? (
          <>
            <dl>
              <div>
                <dt>Task ID</dt>
                <dd>
                  <code>{job.task_id}</code>
                </dd>
              </div>
              <div>
                <dt>状态</dt>
                <dd>
                  {statusLabel(job.status)} · {job.progress_percent}%
                </dd>
              </div>
            </dl>
            {job.status === "failed" ? (
              <div className="collector-export-error">
                <strong>导出失败</strong>
                <p>{job.error ?? "请检查可用空间和所选范围后重新创建任务。"}</p>
              </div>
            ) : null}
            {job.artifact ? (
              <section className="collector-export-artifact">
                <h3>生成产物</h3>
                <dl>
                  <div>
                    <dt>文件名</dt>
                    <dd>{job.artifact.file_name}</dd>
                  </div>
                  <div>
                    <dt>大小</dt>
                    <dd>{formatBytes(job.artifact.size_bytes)}</dd>
                  </div>
                  <div>
                    <dt>包含范围</dt>
                    <dd>
                      {job.artifact.included_sections.map((section) => sectionLabels[section] ?? section).join("、") ||
                        "未提供"}
                    </dd>
                  </div>
                  <div>
                    <dt>SHA-256</dt>
                    <dd>
                      <code>{job.artifact.sha256}</code>
                    </dd>
                  </div>
                  <div>
                    <dt>逻辑位置</dt>
                    <dd>
                      <code>{job.artifact.logical_location}</code>
                    </dd>
                  </div>
                  <div>
                    <dt>生成时间</dt>
                    <dd>{job.artifact.created_at}</dd>
                  </div>
                </dl>
                <a
                  className="btn btn--primary btn--md"
                  aria-label="下载导出文件"
                  href={job.artifact.download_url || exportDownloadUrl(job.task_id)}
                >
                  下载
                </a>
              </section>
            ) : null}
          </>
        ) : null}
      </div>
    </Modal>
  );
}
