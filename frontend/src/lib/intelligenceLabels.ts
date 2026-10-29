export const ATTRIBUTION: Record<string, string> = { project: "项目", kr: "KR", question: "问题", domain: "领域", exploration: "新方向", paused: "暂放方向" };
export const CONTENT_TYPE: Record<string, string> = { event: "事件/新闻", method: "方法/教程", case: "案例/拆解", opinion: "观点/评论", tool: "工具/产品", data: "数据/报告" };
export const ACTION: Record<string, string> = { aware: "知悉", read: "精读", distill_candidate: "沉淀候选", action_candidate: "行动候选" };

export function enumLabel(map: Record<string, string>, value: string | null): string {
  return value === null ? "—" : map[value] ?? value;
}
