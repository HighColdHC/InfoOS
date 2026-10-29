import type { CardPreviewFacets } from "../../../../lib/types/cardPreview";
import { platformLabel } from "../../../../lib/platforms";
import "../../../../styles/_card-source-filters.css";

type Source = CardPreviewFacets["sources"][number];
type Props = {
  publishedSince: string; publishedUntil: string;
  onSinceChange: (value: string) => void; onUntilChange: (value: string) => void;
  sourceId: string; platform: string;
  onSelect: (source: Pick<Source, "source_id" | "platform">) => void;
  onClear: () => void;
  facets: CardPreviewFacets | null; loading: boolean; error: boolean; onRetry: () => void;
};
const sourceKey = (source: Pick<Source, "source_id" | "platform">) => JSON.stringify([source.platform, source.source_id]);

export function CardSourceFilters(props: Props) {
  const sources = props.facets?.sources ?? [];
  const options = sources.filter(source => !props.platform || source.platform === props.platform);
  const selectedKey = props.sourceId ? sourceKey({ platform: props.platform, source_id: props.sourceId }) : "";
  const invalidRange = Boolean(props.publishedSince && props.publishedUntil && props.publishedSince > props.publishedUntil);
  return <div className="card-source-filters">
    <div className="card-source-filters__inputs">
      <label>发布起始日期
        <input type="date" value={props.publishedSince} onChange={event => props.onSinceChange(event.target.value)} />
      </label>
      <label>发布截止日期
        <input type="date" value={props.publishedUntil} onChange={event => props.onUntilChange(event.target.value)} />
      </label>
      <label>来源
        <select aria-label="来源" value={selectedKey} onChange={event => {
          if (!event.target.value) props.onClear();
          else { const [platform, source_id] = JSON.parse(event.target.value); props.onSelect({ platform, source_id }); }
        }}>
          <option value="">全部来源</option>
          {selectedKey && !options.some(source => sourceKey(source) === selectedKey)
            ? <option value={selectedKey}>{props.sourceId === "__none__" ? "未关联来源" : props.sourceId}（当前筛选）</option> : null}
          {options.map(source => <option key={sourceKey(source)} value={sourceKey(source)}>
            {source.name} · {platformLabel(source.platform)}（{source.card_count}）
          </option>)}
        </select>
      </label>
    </div>
    <p className="card-source-filters__hint">按发布时间筛选，起止均含北京时间当日 00:00；仅填截止日期可看此前卡片。填写日期时不含发布时间未知的卡片。</p>
    {invalidRange ? <p role="alert">起始日期不得晚于截止日期。</p> : null}
    <details className="card-source-stats">
      <summary>按来源统计{props.facets ? `（${sources.length} 项）` : ""}</summary>
      <p className="card-source-filters__hint">区间内全部可读卡片，按卡片数降序；不随搜索、平台、状态或推荐筛选变化。点击一行按该来源和平台筛选。</p>
      {props.loading ? <p role="status">正在统计来源…</p> : props.error ? <p role="alert">
        来源统计加载失败，请检查日期区间。<button type="button" onClick={props.onRetry}>重试统计</button>
      </p> : !sources.length ? <p>该区间没有可读卡片。</p> : <div className="card-source-stats__rows">
        {sources.map(source => <button type="button" className="card-source-stats__row" key={sourceKey(source)}
          aria-pressed={selectedKey === sourceKey(source)} onClick={() => props.onSelect(source)}>
          <span className="card-source-stats__name">{source.name}<small>{platformLabel(source.platform)}</small></span>
          <span className="card-source-stats__counts">
            <strong>{source.card_count} 张</strong>
            {source.evaluated_count !== undefined ? <small>已评估 {source.evaluated_count} · 通过 {source.passed_count ?? "—"}</small> : null}
          </span>
        </button>)}
      </div>}
    </details>
  </div>;
}
