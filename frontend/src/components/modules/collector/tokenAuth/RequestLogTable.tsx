import type { RequestLogItem } from "../../../../lib/types/tokenAuth";
import { Button } from "../../../ui/primitives/Button";

export function RequestLogTable({
  items,
  onDetail,
}: {
  items: RequestLogItem[];
  onDetail: (item: RequestLogItem) => void;
}) {
  return (
    <div className="collector-request-table-wrap">
      <table className="collector-request-table" aria-label="Token 请求日志">
        <thead>
          <tr>
            <th>时间</th>
            <th>状态码</th>
            <th>返回结果</th>
            <th>接口</th>
            <th>Token / 客户端</th>
            <th>耗时</th>
            <th>详情</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.request_id}>
              <td>
                {formatDate(item.created_at)}
                <small>{item.request_id}</small>
              </td>
              <td>
                <span
                  className={`collector-request-status collector-request-status--${Math.floor(item.status_code / 100)}xx`}
                >
                  {item.status_code}
                </span>
              </td>
              <td>
                <strong>{item.result_code}</strong>
                <small>{item.result_summary}</small>
              </td>
              <td>
                <code>
                  {item.method} {item.path}
                </code>
              </td>
              <td>
                <span>{item.token_prefix || "无有效 Token"}</span>
                <small>{item.client_label || "未知客户端"}</small>
              </td>
              <td>{item.duration_ms} ms</td>
              <td>
                <Button size="sm" variant="ghost" onClick={() => onDetail(item)} aria-label={`详情 ${item.request_id}`}>
                  详情
                </Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function formatDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat("zh-CN", { dateStyle: "short", timeStyle: "medium" }).format(date);
}
