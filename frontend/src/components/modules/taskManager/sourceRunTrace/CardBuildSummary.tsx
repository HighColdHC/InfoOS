import type { SourceRunTrace } from "../../../../lib/types/sourceRuns";
import { fact } from "./sourceRunTraceViewModel";

export function CardBuildSummary({ stats }: { stats: SourceRunTrace["card_build"] }) {
  return (
    <section className="collector-task-trace-stage">
      <header>
        <div>
          <h3>卡片构建</h3>
          <p>以 CARD_FINALIZE 为信息卡终点</p>
        </div>
      </header>
      <div className="collector-task-stat-row">
        <b>总数 {fact(stats.total)}</b>
        <b>完善 {fact(stats.complete)}</b>
        <b>部分缺失 {fact(stats.partial)}</b>
        <b>失败 {fact(stats.failed)}</b>
        <b>重试 {fact(stats.retry_count)}</b>
      </div>
    </section>
  );
}
