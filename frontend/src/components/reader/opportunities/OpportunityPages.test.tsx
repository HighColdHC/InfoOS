import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fetchOpportunityBoard, fetchOpportunityDetail, fetchOpportunityStats, setOpportunityFollowUp } from "../../../lib/api/opportunities";
import { fetchCardPreviewDetail } from "../../../lib/api/cardPreview";
import type { BoardData, OpportunityItem, StatsData } from "../../../lib/types/opportunities";
import { OpportunityBoardPage } from "./OpportunityBoardPage";
import { OpportunityStatsPage } from "./OpportunityStatsPage";

vi.mock("../../../lib/api/cardPreview", () => ({ fetchCardPreviewDetail: vi.fn() }));
vi.mock("../../../lib/api/opportunities", () => ({ fetchOpportunityBoard: vi.fn(), fetchOpportunityDetail: vi.fn(), fetchOpportunityStats: vi.fn(), setOpportunityFollowUp: vi.fn() }));
const tile: OpportunityItem = {
  candidate_kind: "prefilter",
  source_url: "https://example.test/post", author_url: null, published_at: null,
  card_id: "card-test", title: "课程线索", offer: "角色设计课程", author: "作者甲", platform: "douyin", platform_label: "抖音", source_name: "推荐流",
  monetization_model: "course", model_label: "课程/培训", replicability: 78, credibility: 70, risk_flags: [], risk_labels: [], next_step: "verify", question_count: 1,
  created_at: "2026-10-21T00:00:00Z", stage: "inbox", stage_since: "2026-10-21T00:00:00Z", stale: false, archive_reason: null,
  latest_verification: null, verification_count: 0, follow_up_reason: null,
};
function board(): BoardData {
  return { columns: { inbox: { count: 1, items: [{ ...tile }] }, plan: { count: 0, items: [] }, contact: { count: 0, items: [] }, action: { count: 0, items: [] }, archive: { count: 0, items: [] } },
    today: { inbox: 1, plan_stale: 0, contact: 0, action: 0 }, platforms: [{ platform: "douyin", label: "抖音" }], models: { course: "课程/培训" } };
}
const statistics: StatsData = {
  window: "all", timezone: "Asia/Shanghai", funnel: { visible: 100, checked: 80, matched: 30, broken_down: 20, verify: 5, following: 2, contacted: 1, action: 0 },
  platforms: [{ platform: "douyin", label: "抖音", visible: 100, matched: 30, broken_down: 20, verify: 5 }], daily: [{ date: "2026-10-21", count: 20 }],
  progress: { latest_breakdown_at: "2026-10-21T00:00:00Z", last_24h: 20, pending: 3 }, next_steps: { verify: 5, observe: 10, skip: 5 },
  models: [{ model: "course", label: "课程/培训", verify: 5, observe: 10, skip: 5 }], points: [[78, 70, "verify"]], risks: [{ risk: "unverifiable", label: "无法核实", count: 20 }],
  follow_up: { stages: { inbox: 3, plan: 1, contact: 1, action: 0, archive: 0 }, archive: { dismissed: 0, dropped: 0 }, median_contact_days: null },
};

beforeEach(() => {
  vi.resetAllMocks();
  window.history.replaceState({}, "", "/r/opportunities");
  vi.mocked(fetchCardPreviewDetail).mockRejectedValue(new Error("no preview"));
  vi.mocked(fetchOpportunityBoard).mockResolvedValue(board());
  vi.mocked(fetchOpportunityStats).mockResolvedValue(statistics);
});

describe("商机页面", () => {
  it("跟进时先换列，保存成功后刷新事实", async () => {
    let finish!: (value: { stage: "plan"; stage_since: string; event_id: string }) => void;
    vi.mocked(setOpportunityFollowUp).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
    render(<OpportunityBoardPage />);
    fireEvent.click(await screen.findByRole("button", { name: "跟进" }));
    expect(within(screen.getByRole("region", { name: "准备接触" })).getByText(tile.offer)).toBeInTheDocument();
    const refreshed = board(); refreshed.columns.inbox = { count: 0, items: [] }; refreshed.columns.plan = { count: 1, items: [{ ...tile, stage: "plan" }] };
    vi.mocked(fetchOpportunityBoard).mockResolvedValue(refreshed);
    await act(async () => finish({ stage: "plan", stage_since: "2026-10-22T00:00:00Z", event_id: "follow-1" }));
    expect(setOpportunityFollowUp).toHaveBeenCalledWith(tile.card_id, "following");
    expect(within(screen.getByRole("region", { name: "准备接触" })).getByText(tile.offer)).toBeInTheDocument();
  });

  it("保存失败恢复原列并提示，拖到接触列不写入", async () => {
    let fail!: (error: Error) => void;
    vi.mocked(setOpportunityFollowUp).mockImplementation(() => new Promise((_, reject) => { fail = reject; }));
    render(<OpportunityBoardPage />);
    fireEvent.click(await screen.findByRole("button", { name: "暂不" }));
    expect(within(screen.getByRole("region", { name: "归档" })).getByText(tile.offer)).toBeInTheDocument();
    await act(async () => fail(new Error("network")));
    expect(within(screen.getByRole("region", { name: "新推荐" })).getByText(tile.offer)).toBeInTheDocument();
    expect(screen.getByText("跟进状态保存失败，已恢复原状态，请重试")).toBeInTheDocument();
    const transfer = { setData: vi.fn() };
    fireEvent.dragStart(screen.getByRole("article", { name: tile.offer }), { dataTransfer: transfer });
    fireEvent.drop(screen.getByRole("region", { name: "已接触 · 观察中" }));
    expect(setOpportunityFollowUp).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/这一步要靠核实记录/)).toBeInTheDocument();
  });

  it("归档拖回新推荐发送 reset", async () => {
    const archived = board(); archived.columns.inbox = { count: 0, items: [] }; archived.columns.archive = { count: 1, items: [{ ...tile, stage: "archive", archive_reason: "dismissed" }] };
    vi.mocked(fetchOpportunityBoard).mockResolvedValue(archived);
    vi.mocked(setOpportunityFollowUp).mockResolvedValue({ stage: "inbox", stage_since: tile.created_at, event_id: "reset-1" });
    render(<OpportunityBoardPage />);
    fireEvent.dragStart(await screen.findByRole("article", { name: tile.offer }), { dataTransfer: { setData: vi.fn() } });
    fireEvent.drop(screen.getByRole("region", { name: "新推荐" }));
    await waitFor(() => expect(setOpportunityFollowUp).toHaveBeenCalledWith(tile.card_id, "reset"));
  });

  it("详情抽屉按 Esc 关闭", async () => {
    vi.mocked(fetchOpportunityDetail).mockResolvedValue({ ...tile, price_signal: null, funnel: "咨询后购买", target_customer: null, breakdown_note: "成交证据未知", evidence: ["原卡提到课程"], verify_questions: ["如何交付？"], labels: { monetization_model: "课程/培训", next_step: "值得接触核实" }, timeline: [] });
    render(<OpportunityBoardPage />);
    fireEvent.click(await screen.findByRole("article", { name: tile.offer }));
    expect(await screen.findByText("如何交付？")).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("统计页显示漏斗、平台、分布、风险与范围切换", async () => {
    render(<OpportunityStatsPage />);
    expect(await screen.findByRole("heading", { name: /线索漏斗/ })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "抖音" })).toBeInTheDocument();
    expect(screen.getByText(/无法核实 20/)).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /1 条拆解/ })).toBeInTheDocument();
    expect(screen.getByText("优先接触区")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "近 7 天" }));
    await waitFor(() => expect(fetchOpportunityStats).toHaveBeenLastCalledWith("7d", expect.any(AbortSignal)));
  });
});
