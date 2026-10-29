import { Copy } from "lucide-react";
import type { RequestLogDetail } from "../../../../lib/types/tokenAuth";
import { Modal } from "../../../ui/Modal";
import { Button } from "../../../ui/primitives/Button";

type Props = {
  detail: RequestLogDetail | null;
  loading: boolean;
  error: string | null;
  onClose: () => void;
  onCopy: (value: string, label: string) => void;
};

export function RequestDetailDialog({ detail, loading, error, onClose, onCopy }: Props) {
  if (!loading && !detail && !error) return null;
  return (
    <Modal
      open
      title="请求详情"
      ariaLabel="请求详情"
      onClose={onClose}
      width={760}
      actions={
        detail ? (
          <>
            <Button icon={Copy} onClick={() => onCopy(detail.request_id, "Request ID")}>
              复制 Request ID
            </Button>
            <Button variant="ghost" onClick={onClose}>
              关闭
            </Button>
          </>
        ) : (
          <Button onClick={onClose}>关闭</Button>
        )
      }
    >
      {loading ? (
        <div className="collector-token-state" role="status">
          正在读取请求详情…
        </div>
      ) : null}
      {error ? (
        <div className="collector-token-state is-error" role="alert">
          {error}
        </div>
      ) : null}
      {detail ? (
        <div className="collector-request-detail">
          <dl>
            <Fact label="请求时间" value={detail.created_at} />
            <Fact label="HTTP 方法" value={detail.method} />
            <Fact label="路径" value={detail.path} />
            <Fact label="客户端" value={detail.client_label || "未知"} />
            <Fact label="脱敏 Token" value={detail.token_prefix || "无"} />
            <Fact label="状态码" value={String(detail.status_code)} />
            <Fact label="耗时" value={`${detail.duration_ms} ms`} />
            <Fact label="Request ID" value={detail.request_id} />
            <Fact label="结果代码" value={detail.result_code} />
            <Fact label="返回摘要" value={detail.result_summary} />
          </dl>
          <section>
            <h3>脱敏响应元数据</h3>
            <pre>{JSON.stringify(detail.response_meta, null, 2)}</pre>
          </section>
        </div>
      ) : null}
    </Modal>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}
