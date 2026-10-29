import { useEffect, useState } from "react";
import { fetchResearchRun } from "../../../../lib/api/research";
import type { ResearchRunDetail as ResearchRunDetailModel } from "../../../../lib/types/research";
import { Button } from "../../../ui/primitives/Button";
import { CardReaderModal, type CardReaderTarget } from "../../../reader/CardReaderModal";
import { runStatusLabel } from "../../../../lib/labels";

function when(value?: string | null) {
  return value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "未提供";
}

export function ResearchRunDetail({ researchRunId, onClose }: { researchRunId: string; onClose: () => void }) {
  const [readerTarget, setReaderTarget] = useState<CardReaderTarget | null>(null);
  const [detail, setDetail] = useState<ResearchRunDetailModel | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    setDetail(null);
    void fetchResearchRun(researchRunId, undefined, controller.signal)
      .then(setDetail)
      .catch((reason: unknown) => {
        if (!(reason instanceof DOMException && reason.name === "AbortError"))
          setError(reason instanceof Error ? reason.message : String(reason));
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [researchRunId]);

  async function loadMore() {
    const cursor = detail?.item_page.next_cursor;
    if (!cursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const next = await fetchResearchRun(researchRunId, cursor);
      setDetail((current) =>
        current
          ? { ...next, item_page: { ...next.item_page, items: [...current.item_page.items, ...next.item_page.items] } }
          : next,
      );
    } catch (reason) {
      setError(`加载更多信息卡失败：${reason instanceof Error ? reason.message : String(reason)}`);
    } finally {
      setLoadingMore(false);
    }
  }

  return (
    <section className="research-detail" aria-label="调研批次详情">
      <div className="research-heading">
        <div>
          <h2>调研批次详情</h2>
          <p>批次提交成功表示已排队；以下状态来自已持久化的任务与卡片证据。</p>
        </div>
        <Button size="sm" onClick={onClose}>
          关闭详情
        </Button>
      </div>
      {loading ? <p role="status">正在读取调研批次…</p> : null}
      {error ? (
        <p className="research-error" role="alert">
          {error}
        </p>
      ) : null}
      {detail ? (
        <>
          <div className="research-detail-summary">
            <div>
              <span>名称</span>
              <strong>{detail.research_run.name}</strong>
            </div>
            <div>
              <span>状态</span>
              <strong>{runStatusLabel(detail.research_run.status)}</strong>
            </div>
            <div>
              <span>创建时间</span>
              <strong>{when(detail.research_run.created_at)}</strong>
            </div>
          </div>
          <section aria-label="各平台运行" className="research-platform-runs">
            <h3>各平台运行</h3>
            {detail.platform_runs.map((run) => (
              <article key={`${run.platform}-${run.source_run_id ?? "pending"}`}>
                <strong>{run.label ?? run.platform}</strong>
                <span className={`research-status is-${run.status}`}>{runStatusLabel(run.status)}</span>
                {run.accepted_items != null ? <small>已接纳 {run.accepted_items} 条</small> : null}
                {run.error ? <p role="alert">错误：{run.error}</p> : null}
                {run.limitation ? <p>限制：{run.limitation}</p> : null}
              </article>
            ))}
          </section>
          <section className="research-coverage" aria-label="覆盖证据">
            <h3>覆盖与限制</h3>
            {detail.coverage.length ? (
              <ul>
                {detail.coverage.map((row, index) => (
                  <li key={`${row.platform}-${row.keyword ?? index}`}>
                    <strong>
                      {row.platform}
                      {row.keyword ? ` · ${row.keyword}` : ""}
                    </strong>
                    <span>
                      {row.completeness ? runStatusLabel(row.completeness) : "覆盖度未知"}
                      {row.stop_reason ? ` · ${runStatusLabel(row.stop_reason)}` : ""}
                    </span>
                    {row.limitation ? <small>{row.limitation}</small> : null}
                  </li>
                ))}
              </ul>
            ) : (
              <p>当前尚无覆盖证据。</p>
            )}
          </section>
          <section className="research-cards" aria-label="调研信息卡">
            <h3>信息卡</h3>
            {detail.item_page.items.length ? (
              <ul>
                {detail.item_page.items.map((item) => (
                  <li key={item.card_id}>
                    <div>
                      <strong>{item.title}</strong>
                      <small>
                        {item.platform ?? "未知平台"} · {when(item.published_at)}
                      </small>
                    </div>
                    <Button size="sm" onClick={() => setReaderTarget({ cardId: item.card_id })}>
                      阅读
                    </Button>
                    {item.source_url ? (
                      <a href={item.source_url} target="_blank" rel="noreferrer">
                        打开来源
                      </a>
                    ) : null}
                  </li>
                ))}
              </ul>
            ) : (
              <p>卡片仍在处理或该批次尚无匹配结果。</p>
            )}
            {detail.item_page.next_cursor ? (
              <Button size="sm" loading={loadingMore} onClick={() => void loadMore()}>
                加载更多信息卡
              </Button>
            ) : null}
          </section>
        </>
      ) : null}
      <CardReaderModal target={readerTarget} onClose={() => setReaderTarget(null)} />
    </section>
  );
}
