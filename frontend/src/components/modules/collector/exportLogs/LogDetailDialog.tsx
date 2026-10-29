import { Button } from "../../../ui/primitives/Button";
import { Modal } from "../../../ui/Modal";
import type { OperationLog } from "../../../../lib/types/exportLogs";
export function redactedSummary(log: OperationLog): string {
  return (
    `[${log.timestamp}] ${log.level}/${log.area} ${log.object_type ?? "-"}:${log.object_id ?? "-"}` +
    ` request=${log.request_id ?? "-"} worker=${log.worker_id ?? "-"} ${log.message}`
  );
}
export function LogDetailDialog({
  log,
  loading,
  error,
  onClose,
  onCopy,
}: {
  log: OperationLog | null;
  loading: boolean;
  error: string | null;
  onClose: () => void;
  onCopy: (log: OperationLog) => void;
}) {
  return (
    <Modal
      open={Boolean(log || loading || error)}
      onClose={onClose}
      title="日志详情"
      ariaLabel="日志详情"
      width={720}
      actions={
        <>
          <Button variant="ghost" size="md" type="button" onClick={onClose}>
            关闭
          </Button>
          {log ? (
            <Button variant="primary" size="md" type="button" aria-label="复制脱敏摘要" onClick={() => onCopy(log)}>
              复制脱敏摘要
            </Button>
          ) : null}
        </>
      }
    >
      <div className="collector-log-detail">
        {loading ? <div role="status">正在读取脱敏详情…</div> : null}
        {error ? <div role="alert">{error}</div> : null}
        {log ? (
          <dl>
            <div>
              <dt>时间</dt>
              <dd>{log.timestamp}</dd>
            </div>
            <div>
              <dt>级别 / 区域</dt>
              <dd>
                {log.level} / {log.area}
              </dd>
            </div>
            <div>
              <dt>对象</dt>
              <dd>
                {log.object_type ?? "-"} / {log.object_id ?? "-"}
              </dd>
            </div>
            <div>
              <dt>Request ID</dt>
              <dd>
                <code>{log.request_id ?? "-"}</code>
              </dd>
            </div>
            <div>
              <dt>Worker</dt>
              <dd>
                <code>{log.worker_id ?? "-"}</code>
              </dd>
            </div>
            <div className="is-message">
              <dt>脱敏消息</dt>
              <dd>{log.message}</dd>
            </div>
          </dl>
        ) : null}
      </div>
    </Modal>
  );
}
