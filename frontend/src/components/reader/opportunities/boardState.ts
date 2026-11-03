import type { BoardData, OpportunityItem, ResearchStatus } from "../../../lib/types/opportunities";

export function moveBoard(data: BoardData, item: OpportunityItem, status: ResearchStatus): BoardData {
  const stamp = new Date().toISOString();
  return { columns: data.columns.map(column => {
    if (column.status === item.research_status) return { ...column, total: column.total - 1,
      items: column.items.filter(row => row.knowledge_id !== item.knowledge_id),
      next_offset: column.next_offset === null ? null : column.next_offset - 1 };
    if (column.status !== status) return column;
    const items = [{ ...item, research_status: status, status_changed_at: stamp, updated_at: stamp }, ...column.items];
    if (status === "pending") items.sort((a, b) => b.strength - a.strength || b.created_at.localeCompare(a.created_at) || b.knowledge_id.localeCompare(a.knowledge_id));
    return { ...column, total: column.total + 1, items,
      next_offset: column.next_offset === null ? null : column.next_offset + 1 };
  }) };
}
