import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { SourceRunTrace } from "../../../../lib/types/sourceRuns";
import { SourceRunTraceView } from "./SourceRunTraceView";
import { traceFixture } from "./__tests__/sourceRunTraceTestFixtures";

describe("SourceRunTraceView", () => {
  it("显示部分采集的实际读取数和停止原因", () => {
    const trace: SourceRunTrace = { ...traceFixture, run: { ...traceFixture.run,
      completeness_status: "partial", observed_count: 1, requested_count: 40, stop_reason: "no_progress" },
      task_tracking: [{ ...traceFixture.task_tracking[0], completeness_status: "partial",
        observed_count: 1, requested_count: 40, stop_reason: "no_progress" }] };
    render(<SourceRunTraceView onTaskCancelled={vi.fn()} trace={trace} onCopy={vi.fn()} onRetry={vi.fn()} />);
    expect(screen.getAllByText(/部分完成 1\/40/)).toHaveLength(2);
    expect(screen.getAllByText("no_progress")).toHaveLength(2);
  });
  it("提示较早运行的事件归档查询方式", () => {
    render(<SourceRunTraceView onTaskCancelled={vi.fn()} trace={{ ...traceFixture, events_archived: true, events_hot_window_days: 30 }} onCopy={vi.fn()} onRetry={vi.fn()} />);
    expect(screen.getByRole("note")).toHaveTextContent("较早的运行事件已归档，可在服务器用归档查询命令查看");
  });
  it("shows the frozen run summary and three-level XHS trace without downstream products", () => {
    render(<SourceRunTraceView onTaskCancelled={vi.fn()} trace={traceFixture} onCopy={vi.fn()} onRetry={vi.fn()} />);
    expect(screen.getByText("链接总数 10")).toBeInTheDocument();
    expect(screen.getByText("去重剔除 3")).toBeInTheDocument();
    expect(screen.getByText("需要抓取 7")).toBeInTheDocument();
    expect(screen.getByText("完善 6")).toBeInTheDocument();
    expect(screen.getByText("部分缺失 1")).toBeInTheDocument();
    expect(screen.getByText("重试 1")).toBeInTheDocument();
    expect(screen.queryByText(/AI 总结|决策|输出|Topic/)).not.toBeInTheDocument();
  });

  it("shows queue tracking for the fetch task and descendants, including overdue and unclaimed truth", () => {
    render(<SourceRunTraceView onTaskCancelled={vi.fn()} trace={traceFixture} onCopy={vi.fn()} onRetry={vi.fn()} />);
    const tracking = screen.getByRole("region", { name: "任务队列追踪" });
    expect(within(tracking).getByText("FETCH")).toBeInTheDocument();
    expect(within(tracking).getByText("浏览器采集")).toBeInTheDocument();
    expect(within(tracking).getByText(/截止 .*已逾期/)).toBeInTheDocument();
    expect(within(tracking).getByText("worker-browser-1")).toBeInTheDocument();
    expect(within(tracking).getByText("未认领")).toBeInTheDocument();
  });

  it("keeps the absence of task tracking explicit", () => {
    render(<SourceRunTraceView onTaskCancelled={vi.fn()} trace={{ ...traceFixture, task_tracking: [] }} onCopy={vi.fn()} onRetry={vi.fn()} />);
    expect(screen.getByText("暂无此来源运行的持久化队列追踪证据。")).toBeInTheDocument();
  });

  it("expands the first two items only and toggles one item without closing", async () => {
    const user = userEvent.setup();
    render(<SourceRunTraceView onTaskCancelled={vi.fn()} trace={traceFixture} onCopy={vi.fn()} onRetry={vi.fn()} />);
    expect(screen.getByRole("region", { name: "笔记 1 抓取树" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "笔记 2 抓取树" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "笔记 3 抓取树" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "展开笔记 3 抓取树" }));
    expect(screen.getByRole("region", { name: "笔记 3 抓取树" })).toBeInTheDocument();
  });

  it("shows persisted CONTENT_ENRICH evidence for each expanded item", () => {
    render(<SourceRunTraceView onTaskCancelled={vi.fn()} trace={traceFixture} onCopy={vi.fn()} onRetry={vi.fn()} />);
    const first = screen.getByRole("region", { name: "笔记 1 抓取树" });
    expect(within(first).getByText("正文处理")).toBeInTheDocument();
    expect(within(first).getByText("task-enrich-1")).toBeInTheDocument();
    expect(within(first).getByText(/第 1\/3 次尝试/)).toBeInTheDocument();
    const second = screen.getByRole("region", { name: "笔记 2 抓取树" });
    expect(within(second).getByText("正文处理证据不可用")).toBeInTheDocument();
  });

  it("keeps OpenCLI evidence collapsed, exposes unavailable truth, and confirms exact failed branch retry", async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn().mockResolvedValue(undefined);
    render(<SourceRunTraceView onTaskCancelled={vi.fn()} trace={traceFixture} onCopy={vi.fn()} onRetry={onRetry} />);
    expect(screen.queryByText("infoos-xhs")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "查看 OpenCLI 请求证据" }));
    expect(screen.getByText("infoos-xhs")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "任务队列追踪" })).getByText("task-fetch-001")).toBeInTheDocument();
    expect(screen.getByText("证据不可用")).toBeInTheDocument();
    const note = screen.getByRole("region", { name: "笔记 1 抓取树" });
    await user.click(within(note).getByRole("button", { name: "重试任务 task-video-1" }));
    const confirm = screen.getByRole("alertdialog", { name: "确认重试失败分支" });
    expect(within(confirm).getByText("task-video-1")).toBeInTheDocument();
    await user.click(within(confirm).getByRole("button", { name: "确认创建新尝试" }));
    expect(onRetry).toHaveBeenCalledWith("task-video-1");
  });

  it("合并显示已退役步骤计数，不提供展开或重试", () => {
    render(<SourceRunTraceView onTaskCancelled={vi.fn()} trace={{ ...traceFixture, retired_steps: { count: 12, availability: "available" } }} onCopy={vi.fn()} onRetry={vi.fn()} />);
    const line = screen.getByText("已退役步骤：12");
    expect(line).toHaveStyle({ color: "var(--color-text-muted)" });
    expect(within(line).queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByText(/AI 内容处理|供应商|自动恢复/)).not.toBeInTheDocument();
  });

  it("退役步骤证据不完整时提示计数范围", () => {
    render(<SourceRunTraceView onTaskCancelled={vi.fn()} trace={{ ...traceFixture, retired_steps: { count: 0, availability: "partial" } }} onCopy={vi.fn()} onRetry={vi.fn()} />);
    expect(screen.getByText("已退役步骤：0（证据不完整，仅统计已读取任务）")).toBeInTheDocument();
  });

  it("无退役步骤时不显示计数行", () => {
    render(<SourceRunTraceView onTaskCancelled={vi.fn()} trace={{ ...traceFixture, retired_steps: { count: 0, availability: "available" } }} onCopy={vi.fn()} onRetry={vi.fn()} />);
    expect(screen.queryByText(/已退役步骤/)).not.toBeInTheDocument();
  });
});
