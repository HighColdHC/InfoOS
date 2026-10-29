import type { SourceCadenceSuggestion, SourceManagementItem } from "../../../../lib/types/sourceManagement";
import { platformLabel } from "../../../../lib/platforms";
import { Button } from "../../../ui/primitives/Button";
import { PlatformMark } from "../../../ui/PlatformMark";

type Props = {
  items: SourceManagementItem[];
  busySourceId: string | null;
  cadenceSuggestions: Partial<Record<string, SourceCadenceSuggestion>>;
  onAdoptCadence: (source: SourceManagementItem, hours: number) => void;
  onDetail: (source: SourceManagementItem) => void;
  onEdit: (source: SourceManagementItem) => void;
  onToggle: (source: SourceManagementItem) => void;
  onRun: (source: SourceManagementItem) => void;
};

export function SourceTable({ items, cadenceSuggestions, busySourceId, onAdoptCadence, onDetail, onEdit, onToggle, onRun }: Props) {
  return (
    <div className="collector-source-table-wrap">
      <table className="collector-source-table">
        <thead>
          <tr>
            <th>昵称</th>
            <th>平台 / 健康</th>
            <th>类型</th>
            <th>稳定标识</th>
            <th>采集频率</th>
            <th>启用</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {items.map((source) => {
            const busy = busySourceId === source.source_id;
            const suggestion = cadenceSuggestions[source.source_id];
            const suggestedHours = suggestion?.suggested_cadence_hours;
            return (
              <tr key={source.source_id}>
                <td data-label="昵称">
                  <div className="platform-cell">
                    <PlatformMark platform={source.platform} />
                    <div>
                      <strong>{source.display_name}</strong>
                      <small>{source.source_id}</small>
                    </div>
                  </div>
                </td>
                <td data-label="平台 / 健康">
                  <span>{platformLabel(source.platform)}</span>
                  <Health health={source.health_status} label={source.health_label} />
                  <small>{source.status_explanation}</small>
                </td>
                <td data-label="类型">{source.source_type}</td>
                <td data-label="稳定标识">
                  <code>{source.identifier}</code>
                </td>
                <td data-label="采集频率">
                  <div className="collector-source-cadence">
                    <span>每 {source.cadence_hours} 小时</span>
                    <small>{source.latest_run_at ? `最近：${formatTime(source.latest_run_at)}` : "尚未运行"}</small>
                    {suggestion && suggestedHours != null ? (
                      <>
                        <small title={`${suggestion.reason}（近 7 天新增 ${suggestion.new_items_7d} 条）`}>建议：每 {suggestedHours} 小时 · 7 天 {suggestion.new_items_7d} 条</small>
                        {suggestedHours !== source.cadence_hours ? (
                          <Button size="sm" variant="ghost" disabled={busy} onClick={() => onAdoptCadence(source, suggestedHours)}
                            aria-label={`采用建议间隔 ${source.display_name}`}>
                            一键采用
                          </Button>
                        ) : null}
                      </>
                    ) : null}
                  </div>
                </td>
                <td data-label="启用">
                  <span className={`collector-source-switch ${source.enabled ? "is-enabled" : ""}`}>
                    {source.enabled ? "启用" : "停用"}
                  </span>
                </td>
                <td data-label="操作">
                  <div className="collector-source-actions">
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={busy}
                      onClick={() => onDetail(source)}
                      aria-label={`详情 ${source.display_name}`}
                    >
                      详情
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={busy}
                      onClick={() => onEdit(source)}
                      aria-label={`编辑 ${source.display_name}`}
                    >
                      编辑
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={busy}
                      onClick={() => onRun(source)}
                      aria-label={`运行 ${source.display_name}`}
                    >
                      运行
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={busy}
                      onClick={() => onToggle(source)}
                      aria-label={`${source.enabled ? "停用" : "启用"} ${source.display_name}`}
                    >
                      {source.enabled ? "停用" : "启用"}
                    </Button>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Health({ health, label }: { health: string; label: string }) {
  return (
    <span className={`collector-source-health collector-source-health--${health}`}>
      <i />
      {label}
    </span>
  );
}
function formatTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(
        date,
      );
}
