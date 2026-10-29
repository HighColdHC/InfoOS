import { useEffect, useRef, useState } from "react";
import { fetchOpportunityDetail } from "../../../lib/api/opportunities";
import type { OpportunityDetail, Stage } from "../../../lib/types/opportunities";
import { fetchCardPreviewDetail } from "../../../lib/api/cardPreview";
import type { CardPreviewDetail } from "../../../lib/types/cardPreview";
import { ReaderContent } from "../ReaderContent";
import { CardReaderModal, type CardReaderTarget } from "../CardReaderModal";
import { cardReaderLinkProps } from "../cardReaderLink";
import { OpportunitySource } from "./OpportunitySource";
import { dateText, STAGES } from "./shared";

export function OpportunityDrawer({ cardId, revision, busy, onClose, onMove, notify }: {
  cardId: string; revision: number; busy: boolean; onClose: () => void; onMove: (detail: OpportunityDetail, stage: Stage) => void; notify: (text: string) => void;
}) {
  const [readerTarget, setReaderTarget] = useState<CardReaderTarget | null>(null);
  const [detail, setDetail] = useState<OpportunityDetail | null>(null);
  const [preview, setPreview] = useState<CardPreviewDetail | null>(null);
  const [previewError, setPreviewError] = useState(false);
  const [tab, setTab] = useState<"original" | "analysis">("original");
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  const drawer = useRef<HTMLElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const controller = new AbortController();
    void fetchOpportunityDetail(cardId, controller.signal).then(value => { if (!controller.signal.aborted) { setDetail(value); setError(false); } },
      () => { if (!controller.signal.aborted) setError(true); });
    void fetchCardPreviewDetail(cardId, controller.signal).then(value => {
      if (!controller.signal.aborted) { setPreview(value); setPreviewError(false); }
    }, () => { if (!controller.signal.aborted) setPreviewError(true); });
    return () => controller.abort();
  }, [cardId, revision, retry]);
  useEffect(() => {
    const previous = document.activeElement;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeButton.current?.focus();
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      if (event.key === "Tab" && drawer.current !== null) {
        const nodes = [...drawer.current.querySelectorAll<HTMLElement>('a[href], button:not(:disabled), input, select, textarea, summary, video[controls], [tabindex="0"]')].filter(node => node.getClientRects().length > 0);
        const first = nodes[0], last = nodes[nodes.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    };
    document.addEventListener("keydown", keydown);
    return () => { document.body.style.overflow = overflow; document.removeEventListener("keydown", keydown); if (previous instanceof HTMLElement) previous.focus(); };
  }, [onClose]);
  async function copy(text: string, message: string) {
    try { await navigator.clipboard.writeText(text); notify(message); }
    catch { notify("复制失败，请手动复制页面文字"); }
  }
  return <><div className="scrim" onClick={onClose} /><aside className="drawer" role="dialog" aria-modal="true" aria-labelledby="opportunity-title" ref={drawer}>
    <button className="drawer__close" aria-label="关闭" ref={closeButton} onClick={onClose}>✕</button>
    <div className="drawer__head">
      <h2 id="opportunity-title">{detail === null ? "商机详情" : detail.offer}</h2>
      {detail !== null ? <><OpportunitySource source={detail} />
        <div className="tile__top"><span className="chip chip--model">{detail.labels.monetization_model}</span>{detail.candidate_kind === "case" ? <span className="chip chip--model">案例</span> : null}<span>{detail.labels.next_step} · agent 拆解于 {dateText(detail.created_at)}</span><span>{cardId}</span></div>
        <div className="drawer__stage" role="group" aria-label="阶段">{STAGES.map(stage => <button key={stage.key}
          className="stagebtn" disabled={busy} aria-pressed={detail.stage === stage.key} onClick={() => onMove(detail, stage.key)}>{stage.name}</button>)}
          {detail.stage === null ? <span>未进入看板</span> : null}</div></> : null}
    </div>
    <div className="drawer__tabs" role="tablist" aria-label="详情内容">
      {([['original', '原文'], ['analysis', '拆解']] as const).map(([key, label]) => <button key={key} id={`opportunity-tab-${key}`} role="tab" aria-selected={tab === key} aria-controls={`opportunity-panel-${key}`} onClick={() => setTab(key)}>{label}</button>)}
    </div>
    <div className="drawer__panes">
      <div className={`drawer__original${tab === "original" ? " is-active" : ""}`} id="opportunity-panel-original" role="tabpanel" aria-labelledby="opportunity-tab-original">
        {previewError ? <p role="alert">原文读取失败。<button className="btn" onClick={() => setRetry(value => value + 1)}>重试</button></p>
          : preview === null ? <p role="status">正在加载原文…</p> : <ReaderContent detail={preview} />}
      </div>
      <div className={`drawer__body${tab === "analysis" ? " is-active" : ""}`} id="opportunity-panel-analysis" role="tabpanel" aria-labelledby="opportunity-tab-analysis">
        {detail !== null && detail.stage === "plan" ? <div className="callout">联系对方时按下面的问题问清楚；聊完后点“记录结果”，把对方怎么说的补上发给 agent。</div> : null}
        {error ? <p role="alert">详情读取失败。<button className="btn" onClick={() => setRetry(value => value + 1)}>重试</button></p> : detail === null ? <p role="status">正在加载详情…</p> : <>
        <section><h4>接触时要问清</h4>{detail.verify_questions.length ? <ol className="qs">{detail.verify_questions.map(question => <li key={question}>{question}</li>)}</ol> : <p>尚无核实问题</p>}</section>
        <section><h4>跟进记录</h4><ol className="timeline">{detail.timeline.map(entry => <li key={entry.id}><time dateTime={entry.created_at}>{dateText(entry.created_at)}</time>
          {entry.summary}{entry.note !== null ? <div className="rec">{entry.note}</div> : null}</li>)}</ol></section>
        <section><h4>agent 拆解</h4><dl className="kv"><dt>建议</dt><dd>{detail.labels.next_step}</dd><dt>价格</dt><dd>{detail.price_signal === null ? "未知" : detail.price_signal}</dd>
          <dt>成交路径</dt><dd>{detail.funnel}</dd><dt>目标客户</dt><dd>{detail.target_customer === null ? "未知" : detail.target_customer}</dd>
          <dt>评分</dt><dd>可复制性 {detail.replicability} · 可信度 {detail.credibility}</dd><dt>风险</dt><dd>{detail.risk_labels.join("、") || "无已标记风险"}</dd>
          <dt>证据</dt><dd><ul>{detail.evidence.map(evidence => <li key={evidence}>{evidence}</li>)}</ul></dd><dt>说明</dt><dd>{detail.breakdown_note}</dd></dl></section>
      </>}</div></div>
    <div className="drawer__foot"><a className="btn btn--lg" {...cardReaderLinkProps(cardId, id => setReaderTarget({ cardId: id }))}>打开原卡</a>
      <button className="btn btn--lg" disabled={detail === null} onClick={() => { if (detail !== null) void copy(detail.verify_questions.map((question, index) => `${index + 1}. ${question}`).join("\n"), "问题已复制"); }}>复制问题</button>
      {detail !== null && (detail.stage === "plan" || detail.stage === "contact") ? <button className="btn btn--lg btn--primary" onClick={() => void copy(`我联系了 InfoOS 商机 ${cardId}（${detail.offer}）。结果：\n\n请按核实记录整理，缺项追问我，确认后再写回。`, "已复制，补上联系结果后发给 agent")}>记录结果（复制给 agent）</button> : null}</div>
  </aside><CardReaderModal target={readerTarget} onClose={() => setReaderTarget(null)} /></>;
}
