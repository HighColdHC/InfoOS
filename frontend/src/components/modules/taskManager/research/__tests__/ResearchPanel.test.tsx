import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { ResearchPanel } from "../ResearchPanel";
import * as api from "../../../../../lib/api/research";

vi.mock("../../../../../lib/api/research", () => ({
  fetchResearchCapabilities: vi.fn(), fetchResearchRuns: vi.fn(), createResearchRun: vi.fn(),
}));
vi.mock("../ResearchRunDetail", () => ({ ResearchRunDetail: () => null }));

beforeEach(() => {
  vi.mocked(api.fetchResearchCapabilities).mockResolvedValue([
    { platform: "xiaohongshu", label: "小红书", research: { status: "available", max_items_limit: 100, supports_date_range: true,
      requires_source_selection: false, sort_options: ["general", "latest", "most-liked", "most-commented", "most-collected"], default_sort: "general" } },
    { platform: "douyin", label: "抖音", research: { status: "available", max_items_limit: 30, supports_date_range: true,
      requires_source_selection: false, sort_options: ["general", "latest", "most-liked"], default_sort: "general" } },
    { platform: "github", label: "GitHub", research: { status: "available", max_items_limit: 100, supports_date_range: true,
      requires_source_selection: false, sort_options: ["latest"], default_sort: "latest" } },
  ]);
  vi.mocked(api.fetchResearchRuns).mockResolvedValue({ ok: true, research_runs: [] });
  vi.mocked(api.createResearchRun).mockResolvedValue({ ok: true, replayed: false,
    research_run: { research_run_id: "test", name: "调研", status: "queued", keywords: ["AI"] } });
});

it("uses per-platform default sorting and submits the selected sort", async () => {
  render(<ResearchPanel />);
  fireEvent.click(await screen.findByLabelText("小红书"));
  fireEvent.click(screen.getByLabelText("抖音"));
  fireEvent.click(screen.getByLabelText("GitHub"));
  expect(screen.getByLabelText("小红书 排序方式")).toHaveValue("general");
  expect(screen.getByLabelText("抖音 排序方式")).toHaveValue("general");
  expect(screen.queryByLabelText("GitHub 排序方式")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("小红书 排序方式"), { target: { value: "most-collected" } });
  fireEvent.change(screen.getByLabelText("调研名称"), { target: { value: "调研" } });
  fireEvent.change(screen.getByLabelText("调研关键词"), { target: { value: "AI" } });
  fireEvent.click(screen.getByRole("button", { name: "提交调研任务" }));
  await waitFor(() => expect(api.createResearchRun).toHaveBeenCalledWith(expect.objectContaining({ platforms: [
    expect.objectContaining({ platform: "xiaohongshu", sort: "most-collected" }),
    expect.objectContaining({ platform: "douyin", sort: "general" }),
    expect.objectContaining({ platform: "github", sort: "latest" }),
  ] }), expect.any(String)));
});
