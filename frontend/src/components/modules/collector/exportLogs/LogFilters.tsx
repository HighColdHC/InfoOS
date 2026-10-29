import { Button } from "../../../ui/primitives/Button";
import type { FormEvent } from "react";
export type LogFilterDraft = { query: string; level: string; area: string; timeFrom: string; timeTo: string };
export function LogFilters({
  value,
  onChange,
  onSubmit,
  onReset,
}: {
  value: LogFilterDraft;
  onChange: (next: LogFilterDraft) => void;
  onSubmit: () => void;
  onReset: () => void;
}) {
  function submit(event: FormEvent) {
    event.preventDefault();
    onSubmit();
  }
  return (
    <form className="collector-log-filters" onSubmit={submit}>
      <label>
        <span>搜索</span>
        <input
          aria-label="搜索日志"
          value={value.query}
          onChange={(event) => onChange({ ...value, query: event.target.value })}
          placeholder="来源、任务、Request ID 或消息"
        />
      </label>
      <label>
        <span>级别</span>
        <select
          aria-label="日志级别"
          value={value.level}
          onChange={(event) => onChange({ ...value, level: event.target.value })}
        >
          <option value="">全部</option>
          <option value="ERROR">ERROR</option>
          <option value="WARNING">WARNING</option>
          <option value="INFO">INFO</option>
        </select>
      </label>
      <label>
        <span>区域</span>
        <select
          aria-label="日志区域"
          value={value.area}
          onChange={(event) => onChange({ ...value, area: event.target.value })}
        >
          <option value="">全部</option>
          {["card", "fetch", "media", "asr", "browser", "interface", "storage", "system"].map((area) => (
            <option key={area}>{area}</option>
          ))}
        </select>
      </label>
      <label>
        <span>开始</span>
        <input
          type="datetime-local"
          value={value.timeFrom}
          onChange={(event) => onChange({ ...value, timeFrom: event.target.value })}
        />
      </label>
      <label>
        <span>结束</span>
        <input
          type="datetime-local"
          value={value.timeTo}
          onChange={(event) => onChange({ ...value, timeTo: event.target.value })}
        />
      </label>
      <div>
        <Button variant="primary" size="sm" type="submit">
          查询日志
        </Button>
        <Button variant="ghost" size="sm" type="button" onClick={onReset}>
          重置
        </Button>
      </div>
    </form>
  );
}
