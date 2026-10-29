import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { fetchAllOpportunities, fetchOpportunityBoard, fetchOpportunityDetail } from "../../../lib/api/opportunities";
import { fetchCardPreviewDetail } from "../../../lib/api/cardPreview";
import type { CardPreviewDetail } from "../../../lib/types/cardPreview";
import type { BoardData, OpportunityDetail } from "../../../lib/types/opportunities";
import { OpportunityBoardPage } from "./OpportunityBoardPage";

vi.mock("../../../lib/api/opportunities", () => ({ fetchAllOpportunities: vi.fn(), fetchOpportunityBoard: vi.fn(), fetchOpportunityDetail: vi.fn(), setOpportunityFollowUp: vi.fn() }));
vi.mock("../../../lib/api/cardPreview", () => ({ fetchCardPreviewDetail: vi.fn() }));
const detail: OpportunityDetail = {
  candidate_kind: "case", card_id: "outside-board", title: "商机标题", offer: "课程服务", author: "醒目账号", platform: "douyin", platform_label: "抖音", source_name: "推荐流",
  source_url: "https://example.test/post", author_url: "https://example.test/account", published_at: "2026-10-27T00:00:00Z",
  stage: null, stage_since: null, stale: false, price_signal: null, funnel: "咨询成交", target_customer: null, replicability: 30, credibility: 40,
  breakdown_note: "需要核实", created_at: "2026-10-27T00:00:00Z", evidence: ["原帖"], verify_questions: ["如何交付？"], risk_labels: [],
  labels: { monetization_model: "课程/培训", next_step: "暂不考虑" }, timeline: [],
};
const preview: CardPreviewDetail = {
  knowledge_cards: [],
  opportunity: { has_breakdown: true, next_step: "skip" },
  card: { card_id: "outside-board", title: "原文标题", author: "醒目账号", platform: "douyin", source_url: "https://example.test/post", published_at: "2026-10-27T00:00:00Z", completeness_status: "complete", first_seen_at: null, version: 1, updated_at: null,
    captured_at: null, status: "active", content_hash: "hash", processor_version: null, source_label: "推荐流", raw_item_id: null, source_run_id: null, attachment_count: 2, engagement_metrics: {} },
  body_blocks: [{ block_id: "body", layer: "original", block_kind: "card_content", content: "原文中的完整正文", status: "preserved", provenance: null, card_id: "outside-board", source_url: null, sort_order: 0 },
    { block_id: "transcript", layer: "derived", block_kind: "transcript", content: "视频转录内容", status: "preserved", provenance: null, card_id: "outside-board", source_url: null, sort_order: 0 }],
  comment_blocks: [{ block_id: "comment", layer: "original", block_kind: "platform_comments", content: "用户评论内容", status: "preserved", provenance: null, card_id: "outside-board", source_url: null, sort_order: 0 }],
  image_assets: [{ card_id: "outside-board", asset_id: "image", asset_kind: "image", asset_url: "/image.png" }], video_assets: [{ card_id: "outside-board", asset_id: "video", asset_kind: "video", asset_url: "/video.mp4" }],
  media_gap: null, missing_reasons: [], evaluation: null, trace: {},
};

beforeEach(() => {
  vi.resetAllMocks();
  window.history.replaceState({}, "", "/r/opportunities");
  vi.mocked(fetchOpportunityBoard).mockResolvedValue({ columns: { inbox: { count: 0, items: [] }, plan: { count: 0, items: [] }, contact: { count: 0, items: [] }, action: { count: 0, items: [] }, archive: { count: 0, items: [] } }, today: { inbox: 0, plan_stale: 0, contact: 0, action: 0 }, platforms: [], models: {} } satisfies BoardData);
  vi.mocked(fetchOpportunityDetail).mockResolvedValue(detail);
  vi.mocked(fetchCardPreviewDetail).mockResolvedValue(preview);
});

it.each([null, "inbox", "plan", "contact", "action", "archive"] as const)("深链接直接打开阶段 %s，展示原文和账号，关闭清除参数", async stage => {
  window.history.replaceState({}, "", "/r/opportunities?card=outside-board&keep=1");
  vi.mocked(fetchOpportunityDetail).mockResolvedValue({ ...detail, stage });
  render(<OpportunityBoardPage />);
  const drawer = await screen.findByRole("dialog");
  expect(await within(drawer).findByText("原文中的完整正文", {}, { timeout: 5000 })).toBeInTheDocument();
  expect(await within(drawer).findByText("视频转录内容", {}, { timeout: 5000 })).toBeInTheDocument();
  expect(await within(drawer).findByText("用户评论内容", {}, { timeout: 5000 })).toBeInTheDocument();
  expect(within(drawer).getAllByText("醒目账号").length).toBeGreaterThan(0);
  expect(within(drawer).getByText("案例")).toBeInTheDocument();
  expect(within(drawer).getByRole("link", { name: "查看原帖" })).toHaveAttribute("href", "https://example.test/post");
  expect(within(drawer).getByRole("link", { name: "账号主页" })).toHaveAttribute("href", "https://example.test/account");
  expect(within(drawer).getByRole("img", { name: "卡片图片附件" })).toBeInTheDocument();
  expect(within(drawer).getByLabelText("视频附件")).toBeInTheDocument();
  if (stage !== null) expect(within(drawer).getByRole("group", { name: "阶段" }).querySelector('[aria-pressed="true"]')).toHaveTextContent({ inbox: "新推荐", plan: "准备接触", contact: "已接触", action: "转入行动", archive: "归档" }[stage]);
  fireEvent.click(within(drawer).getByRole("button", { name: "关闭" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(window.location.search).toBe("?keep=1");
}, 15000);

it("全部视图按卡号搜索", async () => {
  vi.mocked(fetchAllOpportunities).mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0, platforms: [], models: {} });
  render(<OpportunityBoardPage />);
  fireEvent.click(screen.getByRole("button", { name: "全部" }));
  await screen.findByText("暂无匹配商机");
  fireEvent.change(screen.getByRole("searchbox", { name: "搜索全部商机" }), { target: { value: "outside-board" } });
  await screen.findByText("暂无匹配商机");
  expect(fetchAllOpportunities).toHaveBeenLastCalledWith(expect.objectContaining({ query: "outside-board" }), 0, expect.any(AbortSignal));
});

it("打开原卡叠加阅读弹窗，Escape 只关闭阅读并回到抽屉", async () => {
  window.history.replaceState({}, "", "/r/opportunities?card=outside-board");
  render(<OpportunityBoardPage />);
  const drawer = await screen.findByRole("dialog");
  await within(drawer).findByRole("heading", { name: "课程服务" });
  const opener = within(drawer).getByRole("link", { name: "打开原卡" });
  opener.focus();
  fireEvent.click(opener);
  const reader = await screen.findByRole("dialog", { name: "原文标题" });
  expect(within(reader).getByRole("link", { name: "新标签页打开" })).toHaveAttribute("href", "/r/card/outside-board");
  fireEvent.keyDown(within(reader).getByRole("button", { name: "关闭对话框" }), { key: "Escape" });
  expect(screen.queryByRole("dialog", { name: "原文标题" })).not.toBeInTheDocument();
  expect(screen.getByRole("dialog", { name: "课程服务" })).toBe(drawer);
  expect(opener).toHaveFocus();
  expect(window.location.search).toBe("?card=outside-board");
  expect(document.body.style.overflow).toBe("hidden");
});
