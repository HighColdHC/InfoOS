import { Button } from "../../../ui/primitives/Button";
import type { SourceRunListItem } from "../../../../lib/types/sourceRuns";
import { dateTime, duration, fact, statusLabel } from "./sourceRunTraceViewModel";

type Props = {
  items: SourceRunListItem[];
  onTrace: (run: SourceRunListItem) => void;
};

export function ResearchRunTable({ items, onTrace }: Props) {
  return (
    <div className="collector-task-table-scroll">
      <table className="collector-task-table collector-task-research-table">
        <thead>
          <tr>
            <th>调研 / 来源</th>
            <th>状态</th>
            <th>平台 / 来源类型</th>
            <th>本次检索语料</th>
            <th>卡片进度</th>
            <th>开始 / 耗时</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {items.map((run) => (
            <tr key={run.source_run_id}>
              <td>
                <strong>{run.source_name}</strong>
                <code>{run.source_run_id}</code>
              </td>
              <td>
                <span className={`collector-task-status is-${run.status}`}>{statusLabel(run.status)}</span>
              </td>
              <td>
                <strong>{run.platform}</strong>
                <small>{run.source_type || "未记录来源类型"}</small>
              </td>
              <td>
                <b>{fact(run.fetch_result.total_links)}</b>
                <small>实际返回结果，受本次 limit 限制</small>
              </td>
              <td>
                <b>
                  {fact(run.card_progress.complete)} / {fact(run.card_progress.partial)} /{" "}
                  {fact(run.card_progress.failed)}
                </b>
                <small>完善 / 部分 / 失败</small>
              </td>
              <td>
                <span>开始：{dateTime(run.started_at)}</span>
                <small>耗时：{duration(run.duration_ms)}</small>
              </td>
              <td>
                {run.trace_available !== false ? (
                  <Button
                    variant="secondary"
                    size="sm"
                    type="button"
                    aria-label={`查看调研 ${run.source_name}`}
                    onClick={() => onTrace(run)}
                  >
                    查看调研
                  </Button>
                ) : (
                  <Button variant="secondary" size="sm" type="button" disabled>
                    等待执行
                  </Button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
