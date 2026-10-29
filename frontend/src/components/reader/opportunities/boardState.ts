import type { BoardData, OpportunityItem, Stage } from "../../../lib/types/opportunities";

export function resetStage(item: OpportunityItem, includeObserve: boolean): Stage | null {
  if (item.latest_verification !== null) return { keep_observing: "contact", to_action: "action", drop: "archive" }[item.latest_verification.conclusion] as Stage;
  return item.next_step === "verify" || (includeObserve && item.next_step === "observe") ? "inbox" : null;
}
export function moveBoard(data: BoardData, item: OpportunityItem, stage: Stage | null, reset = false): BoardData {
  if (item.stage === stage && !reset) return data;
  const columns = { ...data.columns };
  columns[item.stage] = { count: columns[item.stage].count - 1, items: columns[item.stage].items.filter(row => row.card_id !== item.card_id) };
  if (stage !== null) {
    const since = reset ? (item.latest_verification === null ? item.created_at : item.latest_verification.created_at) : new Date().toISOString();
    const stale = stage === "contact" && Date.now() - Date.parse(since) > 14 * 86400000;
    const moved: OpportunityItem = { ...item, stage, stage_since: since, stale,
      archive_reason: stage === "archive" ? (reset ? "dropped" : "dismissed") : null };
    columns[stage] = { count: columns[stage].count + 1, items: [moved, ...columns[stage].items] };
  }
  const today = { ...data.today };
  if (item.stage === "inbox" && Date.now() - Date.parse(item.created_at) < 86400000) today.inbox--;
  if (stage === "inbox" && Date.now() - Date.parse(item.created_at) < 86400000) today.inbox++;
  if (item.stage === "plan" && item.stale) today.plan_stale--;
  if (item.stage === "contact" || item.stage === "action") today[item.stage]--;
  if (stage === "contact" || stage === "action") today[stage]++;
  return { ...data, columns, today };
}
