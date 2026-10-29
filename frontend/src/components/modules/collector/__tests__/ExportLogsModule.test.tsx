import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  estimateExport, fetchExportJob, fetchExportJobs, fetchLogDetail, fetchOperationsLogs, submitExport,
} from "../../../../lib/api/exportLogs";
import type { ExportJobDetail, OperationsLogPage } from "../../../../lib/types/exportLogs";
import { fetchSourceRuns } from "../../../../lib/api/sourceRuns";
import { ToastProvider } from "../../../ui/Toast";
import { ExportLogsModule } from "../ExportLogsModule";

vi.mock("../../../../lib/api/exportLogs", () => ({
  estimateExport: vi.fn(), submitExport: vi.fn(), fetchExportJobs: vi.fn(), fetchExportJob: vi.fn(),
  fetchOperationsLogs: vi.fn(), fetchLogDetail: vi.fn(), exportDownloadUrl: (taskId: string) => `/api/exports/${taskId}/download`,
}));
vi.mock("../../../../lib/api/sourceRuns", () => ({ fetchSourceRuns: vi.fn() }));

const logPage: OperationsLogPage = {
  summary: { total: 26, error: 1, warning: 2 }, total: 26, limit: 25, offset: 0, next_offset: 25,
  items: [{ log_id: "log-safe-1", timestamp: "2026-07-16T08:00:00Z", level: "INFO", area: "fetch", message: "已脱敏消息 <INFOOS_DATA_HOME>", object_type: "task", object_id: "task-1", request_id: "req-1", source_id: "source-1", task_id: "task-1", worker_id: "worker-1" }],
};
const completedJob: ExportJobDetail = {
  task_id: "task-export-1", export_id: "export-1", kind: "data", status: "completed", progress_percent: 100, error: null,
  request: { kind: "data", format: "json", include_sources_runs: true, include_cards_content: true, include_asset_index: true, include_attachment_files: false, include_redacted_logs: false, include_media_files: false, log_levels: [], include_environment_snapshot: false, source_run_ids: [], time_from: null, time_to: null },
  artifact: { file_name: "infoos-data.json", size_bytes: 2048, sha256: "sha256-safe", logical_location: "exports/export-1/infoos-data.json", created_at: "2026-07-16T08:01:00Z", included_sections: ["sources", "cards"], download_url: "/api/exports/task-export-1/download" },
};

function renderModule() { return render(<ToastProvider><ExportLogsModule /></ToastProvider>); }

describe("ExportLogsModule", () => {
  beforeEach(() => {
    vi.clearAllMocks(); window.history.replaceState({}, "", "?menu=logs");
    vi.mocked(fetchExportJobs).mockResolvedValue({ items: [], total: 0, limit: 25, offset: 0, next_offset: null });
    vi.mocked(fetchOperationsLogs).mockResolvedValue(logPage);
    vi.mocked(fetchLogDetail).mockResolvedValue(logPage.items[0]);
    vi.mocked(estimateExport).mockResolvedValue({ normalized_request: completedJob.request, estimated_bytes: 4096, file_count: 3, media_bytes: 1024 });
    vi.mocked(submitExport).mockResolvedValue({ task_id: "task-export-1", export_id: "export-1", status: "pending", progress_url: "/api/exports/task-export-1" });
    vi.mocked(fetchExportJob).mockResolvedValue(completedJob);
    vi.mocked(fetchSourceRuns).mockResolvedValue({ total_exact: true, counts_exact: true, queue_summary: {}, summary: { last_24h: 2, completed: 2, attention: 0, active: 0 }, items: [{ source_run_id: "run-1", source_id: "source-1", source_name: "小红书观察", platform: "xiaohongshu", status: "completed", trigger: "manual", fetch_result: { total_links: 1, dedup_skipped: 0, pending_items: 1, raw_item_count: 1 }, card_progress: { total: 1, complete: 1, partial: 0, failed: 0 }, started_at: "2026-07-16T07:00:00Z", duration_ms: 1000 }], total: 1, next_offset: null });
  });
  afterEach(() => { vi.useRealTimers(); });

  it("shows three non-duplicated presets and server-paged redacted logs without absolute paths", async () => {
    renderModule();
    expect(await screen.findByRole("heading", { name: "导出与备份" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "导出数据" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "立即备份" })).toBeInTheDocument();
    const cards = screen.getByRole("region", { name: "导出能力" });
    expect(within(cards).getAllByRole("article")).toHaveLength(3);
    expect(within(cards).getByText("来源与卡片数据")).toBeInTheDocument();
    expect(within(cards).getByText("完整本地备份")).toBeInTheDocument();
    expect(within(cards).getByText("脱敏诊断包")).toBeInTheDocument();
    expect(screen.getByText("已脱敏消息 <INFOOS_DATA_HOME>")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/\/Users\/|[A-Z]:\\/);
  });

  it("shows candidate loading failure in the diagnostic dialog", async () => {
    vi.mocked(fetchSourceRuns).mockRejectedValueOnce(new Error("source runs unavailable"));
    renderModule();
    fireEvent.click(await screen.findByRole("button", { name: "配置脱敏诊断包" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("候选运行读取失败：source runs unavailable");
  });

  it("debounces real estimate by 300ms and reuses the same data preset from both entries", async () => {
    renderModule(); await screen.findByText("已脱敏消息 <INFOOS_DATA_HOME>");
    vi.useFakeTimers();
    fireEvent.click(screen.getByRole("button", { name: "导出数据" }));
    fireEvent.click(screen.getByLabelText("包含附件实体文件"));
    await act(async () => { vi.advanceTimersByTime(299); });
    expect(estimateExport).not.toHaveBeenCalled();
    await act(async () => { vi.advanceTimersByTime(1); });
    expect(estimateExport).toHaveBeenCalledWith(expect.objectContaining({ kind: "data", include_attachment_files: true }));
    expect(screen.getByText(/4 KB/)).toBeInTheDocument();
  });

  it("submits asynchronously, writes the task URL, polls to terminal, and shows only logical artifact location", async () => {
    const user = userEvent.setup(); renderModule();
    await user.click(await screen.findByRole("button", { name: "立即备份" }));
    await user.click(screen.getByRole("button", { name: "创建异步任务" }));
    expect(submitExport).toHaveBeenCalledWith(expect.objectContaining({ kind: "backup", format: "zip" }));
    expect(new URLSearchParams(window.location.search).get("export_task_id")).toBe("task-export-1");
    expect(await screen.findByText("task-export-1")).toBeInTheDocument();
    expect(await screen.findByText("exports/export-1/infoos-data.json")).toBeInTheDocument();
    expect(screen.getByText("sha256-safe")).toBeInTheDocument();
    expect(screen.getByText("来源、信息卡")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "下载导出文件" })).toHaveAttribute("href", "/api/exports/task-export-1/download");
    expect(document.body.textContent).not.toContain("/Users/");
  });

  it("restores a job from export_task_id and diagnostic preset selects Source Runs from T05", async () => {
    window.history.replaceState({}, "", "?menu=logs&export_task_id=task-export-1");
    vi.mocked(fetchExportJobs).mockResolvedValue({ items: [completedJob], total: 1, limit: 25, offset: 0, next_offset: null });
    const user = userEvent.setup(); renderModule();
    expect(await screen.findByRole("dialog", { name: "导出任务进度" })).toBeInTheDocument();
    expect(fetchExportJob).toHaveBeenCalledWith("task-export-1");
    expect(fetchExportJobs).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "关闭导出任务" }));
    await user.click(screen.getByRole("button", { name: "配置脱敏诊断包" }));
    expect(fetchSourceRuns).toHaveBeenCalledWith(expect.objectContaining({ limit: 25, offset: 0 }), expect.anything());
    await user.click(await screen.findByLabelText("选择 Source Run run-1"));
    await user.click(screen.getByRole("button", { name: "创建异步任务" }));
    expect(submitExport).toHaveBeenLastCalledWith(expect.objectContaining({ kind: "diagnostic", format: "zip", include_environment_snapshot: true, source_run_ids: ["run-1"] }));
  });

  it("runs log filters on the server at offset zero and shows/copies only the redacted detail", async () => {
    const user = userEvent.setup(); renderModule();
    await screen.findByText("已脱敏消息 <INFOOS_DATA_HOME>");
    await user.click(screen.getByRole("button", { name: "下一页日志" }));
    await user.type(screen.getByLabelText("搜索日志"), "req-1");
    await user.selectOptions(screen.getByLabelText("日志级别"), "ERROR");
    await user.click(screen.getByRole("button", { name: "查询日志" }));
    await waitFor(() => expect(fetchOperationsLogs).toHaveBeenLastCalledWith(expect.objectContaining({ query: "req-1", level: "ERROR", offset: 0, limit: 25 }), expect.anything()));
    await user.click(screen.getByRole("button", { name: "查看日志 log-safe-1" }));
    const dialog = await screen.findByRole("dialog", { name: "日志详情" });
    expect(within(dialog).getByText("worker-1")).toBeInTheDocument();
    expect(within(dialog).getByText("req-1")).toBeInTheDocument();
    const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    await user.click(within(dialog).getByRole("button", { name: "复制脱敏摘要" }));
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining("已脱敏消息 <INFOOS_DATA_HOME>"));
    expect(writeText.mock.calls[0][0]).not.toContain("/Users/");
  });
});
