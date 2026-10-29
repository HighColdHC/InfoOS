import type { Dispatch, SetStateAction } from "react";
import { Activity, AlertTriangle, Clock3, RefreshCw, ShieldCheck } from "lucide-react";
import type { RequestLogItem, RequestLogPage } from "../../../../lib/types/tokenAuth";
import { MetricGrid } from "../../../ui/MetricGrid";
import { Button } from "../../../ui/primitives/Button";
import { RequestLogTable } from "./RequestLogTable";

const PAGE_SIZE = 25;
type Props = {
  logs: RequestLogPage | null;
  logsLoading: boolean;
  logsError: string | null;
  queryDraft: string;
  status: string;
  timeRange: string;
  offset: number;
  setLogsRefresh: Dispatch<SetStateAction<number>>;
  setOffset: Dispatch<SetStateAction<number>>;
  setQuery: Dispatch<SetStateAction<string>>;
  setQueryDraft: Dispatch<SetStateAction<string>>;
  setStatus: Dispatch<SetStateAction<string>>;
  setTimeRange: Dispatch<SetStateAction<string>>;
  onDetail: (item: RequestLogItem) => void;
};

export function RequestLogsPanel({ logs, logsLoading, logsError, queryDraft, status, timeRange, offset,
  setLogsRefresh, setOffset, setQuery, setQueryDraft, setStatus, setTimeRange, onDetail }: Props) {
  return (
        <section id="collector-token-panel-requests" role="tabpanel" aria-labelledby="collector-token-tab-requests" className="collector-token-panel">
          <div className="collector-token-heading">
            <div>
              <h2>请求日志</h2>
              <p>核对插件实际认证和卡片 Interface 返回结果。</p>
            </div>
            <Button icon={RefreshCw} loading={logsLoading} onClick={() => setLogsRefresh((value) => value + 1)}>
              刷新日志
            </Button>
          </div>
          {logs ? (
            <MetricGrid
              metrics={[
                {
                  label: timeRange === "24h" ? "最近 24 小时请求数" : "当前筛选请求数",
                  value: logs.summary.request_count,
                  icon: Activity,
                },
                { label: "正常返回", value: logs.summary.success_2xx_count, icon: ShieldCheck },
                { label: "4xx", value: logs.summary.client_error_4xx_count, icon: AlertTriangle },
                { label: "中位耗时", value: `${logs.summary.median_duration_ms} ms`, icon: Clock3 },
              ]}
            />
          ) : null}
          <form
            className="collector-request-filters"
            role="search"
            aria-label="请求日志筛选"
            onSubmit={(event) => {
              event.preventDefault();
              setOffset(0);
              setQuery(queryDraft.trim());
            }}
          >
            <input
              type="search"
              aria-label="搜索请求日志"
              placeholder="Request ID、接口或客户端"
              value={queryDraft}
              onChange={(event) => setQueryDraft(event.target.value)}
            />
            <select
              aria-label="HTTP 状态"
              value={status}
              onChange={(event) => {
                setOffset(0);
                setStatus(event.target.value);
              }}
            >
              <option value="">全部状态</option>
              <option value="2xx">2xx</option>
              <option value="4xx">4xx</option>
              <option value="206">206</option>
              <option value="401">401</option>
              <option value="404">404</option>
            </select>
            <select
              aria-label="时间范围"
              value={timeRange}
              onChange={(event) => {
                setOffset(0);
                setTimeRange(event.target.value);
              }}
            >
              <option value="24h">最近 24 小时</option>
              <option value="7d">最近 7 天</option>
              <option value="30d">最近 30 天</option>
            </select>
          </form>
          {logsLoading && !logs ? (
            <div className="collector-token-state" role="status">
              正在读取请求日志…
            </div>
          ) : null}
          {logsError ? (
            <div className="collector-token-state is-error" role="alert">
              请求日志读取失败：{logsError}
            </div>
          ) : null}
          {logs && !logsError ? (
            <>
              <RequestLogTable items={logs.items} onDetail={onDetail} />
              <div className="collector-request-pagination">
                <span>共 {logs.total} 条</span>
                <div>
                  <Button
                    size="sm"
                    disabled={!offset || logsLoading}
                    onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                  >
                    上一页
                  </Button>
                  <Button
                    size="sm"
                    disabled={logs.next_offset == null || logsLoading}
                    onClick={() => setOffset(logs.next_offset ?? offset)}
                  >
                    下一页
                  </Button>
                </div>
              </div>
            </>
          ) : null}
        </section>
  );
}
