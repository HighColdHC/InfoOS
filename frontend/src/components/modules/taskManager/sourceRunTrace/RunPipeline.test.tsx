import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { SourceRunTraceView } from "./SourceRunTraceView";
import { SourceRunTable } from "./SourceRunTable";
import { traceFixture } from "./__tests__/sourceRunTraceTestFixtures";
import type { SourceRunListItem, SourceRunTrace } from "../../../../lib/types/sourceRuns";

describe("运行流程交互", () => {
  it("整行打开追踪，代码选择与按钮操作不重复触发行点击", async () => {
    const onTrace = vi.fn();
    const user = userEvent.setup();
    const run: SourceRunListItem = { ...traceFixture.run, fetch_result: traceFixture.run.fetch_stats };
    render(<SourceRunTable items={[run]} onTrace={onTrace} />);
    await user.click(screen.getByText(run.source_name));
    expect(onTrace).toHaveBeenCalledTimes(1);
    onTrace.mockClear();
    await user.click(screen.getByText(run.source_run_id));
    expect(onTrace).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: `追踪 ${run.source_name}` }));
    expect(onTrace).toHaveBeenCalledTimes(1);
  });
  it("定位中断篇目后展开并聚焦对应失败节点；原始证据位于队列之后且缺口默认折叠", async () => {
    const scroll = vi.fn();
    const original = HTMLElement.prototype.scrollIntoView;
    HTMLElement.prototype.scrollIntoView = scroll;
    try {
      const item = { ...traceFixture.items[2], stages: [{ task_id: "focused-media", stage: "media_download",
        status: "pending", attempt: 2, max_attempts: 3, retry_at: "2026-10-21T09:30:00Z",
        error: { class: "network_timeout", summary: "下载超时，等待重试" } },
        { task_id: "terminal-media", stage: "media_download", status: "failed", attempt: 3, max_attempts: 3,
          error: { class: "network_timeout", summary: "下载失败，已用尽尝试" } }],
        video_download: { total: 0, success: 0, failed: 0, retry_count: 0, tasks: [] } };
      const trace: SourceRunTrace = { ...traceFixture, items: [item] };
      render(<SourceRunTraceView onTaskCancelled={vi.fn()} trace={trace} blockingReason="演示阻塞原因" onCopy={vi.fn()} onRetry={vi.fn()} />);
      const stop = screen.getByRole("region", { name: "中断于：媒体下载" });
      expect(stop).toHaveTextContent("影响已加载 1 篇");
      expect(stop).toHaveTextContent("尝试 2/3");
      expect(stop).toHaveTextContent("下次重试");
      expect(stop).toHaveTextContent("阻塞原因：演示阻塞原因");
      fireEvent.click(within(stop).getByRole("button", { name: "定位到该篇" }));
      await waitFor(() => expect(document.activeElement).toHaveAttribute("data-task-id", "focused-media"));
      expect(document.activeElement).toHaveClass("is-located");
      expect(scroll).toHaveBeenCalled();
      const queue = screen.getByRole("region", { name: "任务队列追踪" });
      const evidence = screen.getByRole("region", { name: "原始证据" });
      expect(queue.compareDocumentPosition(evidence) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      expect(screen.getByText("证据缺口").closest("details")).not.toHaveAttribute("open");
    } finally { HTMLElement.prototype.scrollIntoView = original; }
  });
});
