import { Button } from "../../../ui/primitives/Button";
import type { FormEvent } from "react";
import type { CollectorPlatformOption } from "../../../../lib/platforms";
import type { SourceRunExecutionScope } from "../../../../lib/types/sourceRuns";

type Props = {
  query: string;
  platform: string;
  platformOptions: CollectorPlatformOption[];
  status: string;
  executionScope: SourceRunExecutionScope;
  timeFrom: string;
  timeTo: string;
  onQuery: (value: string) => void;
  onPlatform: (value: string) => void;
  onStatus: (value: string) => void;
  onExecutionScope: (value: SourceRunExecutionScope) => void;
  onTimeFrom: (value: string) => void;
  onTimeTo: (value: string) => void;
  onSubmit: () => void;
  onReset: () => void;
};

export function SourceRunFilters(props: Props) {
  function submit(event: FormEvent) {
    event.preventDefault();
    props.onSubmit();
  }
  return (
    <form className="collector-task-filters" aria-label="来源运行筛选" onSubmit={submit}>
      <label>
        <span>搜索</span>
        <input
          aria-label="搜索来源运行"
          value={props.query}
          onChange={(event) => props.onQuery(event.target.value)}
          placeholder="来源名称或 ID"
        />
      </label>
      <label>
        <span>平台</span>
        <select aria-label="平台筛选" value={props.platform} onChange={(event) => props.onPlatform(event.target.value)}>
          <option value="">全部平台</option>
          {props.platformOptions.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </label>
      <label>
        <span>状态</span>
        <select aria-label="状态筛选" value={props.status} onChange={(event) => props.onStatus(event.target.value)}>
          <option value="">全部状态</option>
          <option value="completed">已完成</option>
          <option value="attention">有波动</option>
          <option value="running">运行中</option>
          <option value="pending">排队中</option>
        </select>
      </label>
      <label>
        <span>执行范围</span>
        <select
          aria-label="执行范围筛选"
          value={props.executionScope}
          onChange={(event) => props.onExecutionScope(event.target.value as SourceRunExecutionScope)}
        >
          <option value="all">全部</option>
          <option value="executed">已执行</option>
          <option value="upcoming">待执行与未来</option>
        </select>
      </label>
      <label>
        <span>开始时间</span>
        <input
          type="datetime-local"
          aria-label="开始时间"
          value={props.timeFrom}
          onChange={(event) => props.onTimeFrom(event.target.value)}
        />
      </label>
      <label>
        <span>结束时间</span>
        <input
          type="datetime-local"
          aria-label="结束时间"
          value={props.timeTo}
          onChange={(event) => props.onTimeTo(event.target.value)}
        />
      </label>
      <div>
        <Button variant="primary" size="sm" type="submit">
          查询
        </Button>
        <Button variant="ghost" size="sm" type="button" onClick={props.onReset}>
          重置
        </Button>
      </div>
    </form>
  );
}
