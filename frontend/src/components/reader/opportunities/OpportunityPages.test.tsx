import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fetchOpportunityBoard, fetchOpportunityStats, setResearchStatus } from "../../../lib/api/opportunities";
import { RESEARCH_LABELS } from "../../../lib/types/knowledge";
import type { BoardData, OpportunityItem, ResearchStatus, ResearchStatusResult, StatsData } from "../../../lib/types/opportunities";
import { OpportunityBoardPage } from "./OpportunityBoardPage";
import { OpportunityStatsPage } from "./OpportunityStatsPage";

vi.mock("../../../lib/api/opportunities", () => ({ fetchOpportunityBoard: vi.fn(), fetchOpportunityStats: vi.fn(), setResearchStatus: vi.fn() }));
const tile: OpportunityItem = {
  knowledge_id: "kc_one", title: "角色设计课程", subtype: "selling", use_note: "课程生意", strength: 78,
  research_status: "pending", status_changed_at: "2026-10-21T00:00:00Z", created_at: "2026-10-21T00:00:00Z",
  updated_at: "2026-10-21T00:00:00Z", evidence_count: 2, platforms: ["douyin"], origin_freshness: "fresh",
  money_signal: "已有成交", signal_kinds: ["deals"],
};
function board(): BoardData {
  return { columns: Object.entries(RESEARCH_LABELS).map(([status, label]) => ({ status: status as ResearchStatus, label,
    total: status === "pending" ? 1 : 0, items: status === "pending" ? [{ ...tile }] : [], next_offset: null })) };
}
const statistics: StatsData = {
  window: "all", total: 1, by_status: [{ status: "pending", label: "待调研", count: 1 }],
  by_subtype: [{ subtype: "selling", label: "带货/售卖", count: 1 }], by_signal: [{ signal: "deals", count: 1 }],
  daily: [{ date: "2026-10-21", count: 1 }], by_platform: [{ platform: "douyin", label: "抖音", count: 1 }],
};
function drag(target: string) {
  fireEvent.dragStart(screen.getByRole("article", { name: tile.title }), { dataTransfer: { setData: vi.fn() } });
  fireEvent.drop(screen.getByRole("region", { name: target }));
}
beforeEach(() => {
  vi.resetAllMocks();
  window.history.replaceState({}, "", "/r/opportunities");
  vi.mocked(fetchOpportunityBoard).mockResolvedValue(board());
  vi.mocked(fetchOpportunityStats).mockResolvedValue(statistics);
});

describe("商机知识卡看板", () => {
  it("拖动时先换列，保存调研状态并显示知识卡链接", async () => {
    let finish!: (value: ResearchStatusResult) => void;
    vi.mocked(setResearchStatus).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
    render(<OpportunityBoardPage />);
    expect(await screen.findByRole("link", { name: tile.title })).toHaveAttribute("href", "/r/knowledge/kc_one");
    drag("调研中");
    expect(within(screen.getByRole("region", { name: "调研中" })).getByText(tile.title)).toBeInTheDocument();
    expect(setResearchStatus).toHaveBeenCalledWith(tile.knowledge_id, "researching", null);
    await act(async () => finish({ knowledge_id: tile.knowledge_id, research_status: "researching", changed: true }));
    expect(screen.getByText("已移到“调研中”")).toBeInTheDocument();
  });

  it("写失败恢复原列并提示", async () => {
    let fail!: (error: Error) => void;
    vi.mocked(setResearchStatus).mockImplementation(() => new Promise((_, reject) => { fail = reject; }));
    render(<OpportunityBoardPage />);
    await screen.findByRole("article", { name: tile.title });
    drag("放弃");
    expect(within(screen.getByRole("region", { name: "放弃" })).getByText(tile.title)).toBeInTheDocument();
    await act(async () => fail(new Error("network")));
    expect(within(screen.getByRole("region", { name: "待调研" })).getByText(tile.title)).toBeInTheDocument();
    expect(screen.getByText("调研状态保存失败，已恢复原状态，请重试")).toBeInTheDocument();
  });

  it("平台取全量统计，筛选按新接口传递", async () => {
    render(<OpportunityBoardPage />);
    await screen.findByRole("article", { name: tile.title });
    expect(fetchOpportunityStats).toHaveBeenCalledWith("all", expect.any(AbortSignal));
    fireEvent.change(screen.getByLabelText("平台"), { target: { value: "douyin" } });
    fireEvent.change(screen.getByLabelText("子类型"), { target: { value: "selling" } });
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "课程 成交" } });
    await waitFor(() => expect(fetchOpportunityBoard).toHaveBeenLastCalledWith({ query: "课程 成交", platform: "douyin", subtype: "selling" }, expect.any(AbortSignal)));
  });

  it("逐列加载更多，保留其他列", async () => {
    const first = board(); first.columns[0].total = 51; first.columns[0].next_offset = 50;
    vi.mocked(fetchOpportunityBoard).mockResolvedValueOnce(first).mockResolvedValueOnce({ columns: [
      { ...first.columns[0], items: [{ ...tile, knowledge_id: "kc_two", title: "第二页商机" }], next_offset: null },
    ] });
    render(<OpportunityBoardPage />);
    fireEvent.click(await screen.findByRole("button", { name: "加载更多（1 / 51）" }));
    expect(await screen.findByRole("link", { name: "第二页商机" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: tile.title })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "继续观察" })).toBeInTheDocument();
    expect(fetchOpportunityBoard).toHaveBeenLastCalledWith({ query: "", platform: "", subtype: "" }, undefined, "pending", 50);
  });

  it("统计显示五块与范围切换", async () => {
    render(<OpportunityStatsPage />);
    expect(await screen.findByRole("heading", { name: "调研状态分布" })).toBeInTheDocument();
    for (const name of ["子类型分布", "赚钱信号类型", "每日新增", "来源平台"]) expect(screen.getByRole("heading", { name })).toBeInTheDocument();
    expect(screen.getByText("抖音")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "每日新增商机数" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "近 7 天" }));
    await waitFor(() => expect(fetchOpportunityStats).toHaveBeenLastCalledWith("7d", expect.any(AbortSignal)));
  });
});
