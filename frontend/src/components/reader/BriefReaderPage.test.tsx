import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BriefReaderPage } from "./BriefReaderPage";
import { getJson } from "../../lib/api/http";

vi.mock("../../lib/api/http", () => ({ getJson: vi.fn() }));

describe("standalone brief reader", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/r/brief/2026-09-29");
    vi.mocked(getJson).mockReset();
  });

  it("renders frozen groups, card links, and Monday summary without management navigation", async () => {
    vi.mocked(getJson).mockResolvedValue({
      brief_date: "2026-09-29", window_start: "2026-09-27T23:00:00Z",
      window_end: "2026-09-28T23:00:00Z", profile_version: 2,
      counts: { related: 1, exploration: 0, landmark: 0 },
      items: [{ number: 1, group: "related", card_id: "card one", title: "标题",
        platform: "rss", author: "作者", published_at: "2026-09-28T08:00:00Z",
        summary: "摘要", relation_note: "与你的关系", attribution_kind: "project",
        attribution_ref: "item", content_type: "method", suggested_action: "read",
        quality: 90, relevance: 80 }],
      weekly_paused: { count: 2, top: { title: "周内一条", deep_link: "/r/card/card-2" } },
    });
    render(<BriefReaderPage />);
    expect(await screen.findByRole("heading", { name: "1. 标题" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "1. 标题" })).toHaveAttribute("href", "/r/card/card%20one");
    expect(screen.getByText("摘要")).toBeInTheDocument();
    expect(screen.getByText(/过去 7 天沉底 2 条/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "漫步" })).toHaveAttribute("href", "/r/browse");
    expect(screen.queryByRole("link", { name: "在管理面打开" })).not.toBeInTheDocument();
    expect(getJson).toHaveBeenCalledWith("/api/intelligence/daily-brief/2026-09-29", undefined, null);
  });

  it("shows a missing brief state", async () => {
    vi.mocked(getJson).mockRejectedValue(new Error("not found"));
    render(<BriefReaderPage />);
    expect(await screen.findByRole("heading", { name: "日报不存在或暂时无法读取" })).toBeInTheDocument();
  });
  afterEach(() => vi.useRealTimers());

  it.each(["2026-10-20", "2026-10-21"])("disables next day at or beyond the latest date: %s", async (date) => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-20T00:00:00Z"));
    window.history.replaceState({}, "", `/r/brief/${date}`);
    vi.mocked(getJson).mockRejectedValue(new Error("not found"));
    render(<BriefReaderPage />);
    await screen.findByRole("heading", { name: "日报不存在或暂时无法读取" });
    expect(screen.getByText("后一天")).toHaveAttribute("aria-disabled", "true");
    expect(screen.queryByRole("link", { name: "后一天" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "前一天" })).toBeInTheDocument();
  });

  it("renders empty groups in one line and zero statistics without links", async () => {
    vi.mocked(getJson).mockResolvedValue({
      brief_date: "2026-09-29", profile_version: 1, counts: { related: 0, exploration: 0, landmark: 0 }, items: [],
    });
    render(<BriefReaderPage />);
    expect(await screen.findByRole("heading", { name: "新方向 · 今日无条目" })).toBeInTheDocument();
    expect(screen.queryByText("这一组今天没有条目。")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /新方向|相关条目|格局变化/ })).not.toBeInTheDocument();
    expect(screen.getByText("InfoOS 日报 · 2026 年 · 星期二")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "后一天" })).toHaveAttribute("href", "/r/brief/2026-09-30");
  });

});

it("renders knowledge sections, update flags, original links and stock counts", async () => {
  window.history.replaceState({}, "", "/r/brief/2026-10-28");
  vi.mocked(getJson).mockResolvedValue({
    layout: "knowledge_v1", brief_date: "2026-10-28", profile_version: 2,
    counts: { news: 0, opportunity: 1, project: 0, topic: 0, deposit: 0 }, stock: { project: 2, method: 1 },
    items: [{ number: 1, section: "opportunity", knowledge_id: "kc_one", kind: "opportunity", subtype: "selling",
      title: "课程生意", use_note: "可调研的生意", attribution_ref: "商机发现系统", strength: 90,
      is_update: true, deep_link: "/r/knowledge/kc_one", origin_card_link: "/r/card/one" }],
  });
  render(<BriefReaderPage />);
  expect(await screen.findByRole("heading", { name: "商机 · 新增 1" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "1. 课程生意" })).toHaveAttribute("href", "/r/knowledge/kc_one");
  expect(screen.getByText(/证据强度 90 · 更新/)).toBeInTheDocument();
  expect(screen.getByText("存量入库：项目 +2、方法 +1")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "查看原卡" })).toHaveAttribute("href", "/r/card/one");
});
