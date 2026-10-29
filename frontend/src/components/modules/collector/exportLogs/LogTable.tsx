import { Button } from "../../../ui/primitives/Button";
import type { OperationLog } from "../../../../lib/types/exportLogs";
export function LogTable({
  items,
  onDetail,
  onCopy,
}: {
  items: OperationLog[];
  onDetail: (item: OperationLog) => void;
  onCopy: (item: OperationLog) => void;
}) {
  return (
    <div className="collector-log-table-scroll">
      <table className="collector-log-table" aria-label="结构化日志">
        <thead>
          <tr>
            <th>时间</th>
            <th>级别</th>
            <th>区域</th>
            <th>对象</th>
            <th>消息</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.log_id}>
              <td>{item.timestamp}</td>
              <td>
                <b className={`is-${item.level.toLowerCase()}`}>{item.level}</b>
              </td>
              <td>{item.area}</td>
              <td>
                <code>
                  {item.object_type ?? "-"}/{item.object_id ?? "-"}
                </code>
              </td>
              <td>{item.message}</td>
              <td>
                <div>
                  <Button
                    variant="secondary"
                    size="sm"
                    type="button"
                    aria-label={`查看日志 ${item.log_id}`}
                    onClick={() => onDetail(item)}
                  >
                    详情
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    type="button"
                    aria-label={`复制日志 ${item.log_id}`}
                    onClick={() => onCopy(item)}
                  >
                    复制
                  </Button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
