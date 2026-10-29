const capability: Record<string, string> = { available: "可用", limited: "受限", conditional: "有条件", unavailable: "不可用" };
const status: Record<string, string> = {
  queued: "已排队", pending: "排队中", running: "运行中", completed: "已完成", success: "已完成",
  failed: "失败", blocked: "需处理", skipped: "已跳过", cancelled: "已取消", partial: "部分完成",
  complete: "完整", available: "可用", unavailable: "不可用", attention: "有波动",
  succeeded: "已完成", enriched: "已处理", preserved: "已保留", not_started: "未开始",
};
const trigger: Record<string, string> = {
  initial: "首次采集", manual: "手动", scheduled: "定时", retry: "重试", unavailable: "不可用",
  automatic: "自动", planned: "已计划", browser_capture: "浏览器捕获", task: "任务触发",
  feedback: "反馈触发", feed_policy_change: "采集策略变更",
};
export const researchCapabilityLabel = (value: string) => capability[value] ?? value;
export const runStatusLabel = (value: string) => status[value] ?? value;
export const triggerLabel = (value: string) => trigger[value] ?? value;
