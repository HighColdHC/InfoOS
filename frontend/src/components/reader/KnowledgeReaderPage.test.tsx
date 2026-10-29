import { render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import App from "../../App";
import { getJson } from "../../lib/api/http";

vi.mock("../../lib/api/http", () => ({ getJson: vi.fn() }));

beforeEach(() => {
  window.history.replaceState({}, "", "/r/knowledge/kc_one");
  vi.mocked(getJson).mockReset();
});

it("renders ordered Chinese fields, original evidence, conflict, research history and related cards", async () => {
  vi.mocked(getJson).mockResolvedValue({
    knowledge_id: "kc_one", kind: "opportunity", kind_label: "商机", subtype: "selling", subtype_label: "带货/售卖",
    title: "课程生意", attribution_kind: "project", attribution_ref: "InfoOS", use_note: "用于商机发现",
    fields: { business: "卖课程", pricing: null, channels: ["短视频"], business_model: { offer: "课程" } },
    field_schema: { business: { label: "生意" }, pricing: { label: "价格" }, channels: { label: "获客渠道" },
      business_model: { label: "商业模式", properties: { offer: { label: "卖什么" } } } },
    has_conflict: true, research_status: "observe",
    status_history: [{ event_id: "event", value: "observe", note: "用户选择", actor: "用户", created_at: "2026-10-27T12:00:00Z" }],
    evidence: [{ evidence_id: "evidence", card_id: "one", title: "原帖", platform: "zsxq", published_at: "2026-10-27T00:00:00Z",
      quotes: ["原文收入证据"], action: "conflict", note: "收入口径不同", deep_link: "/r/card/one" }],
    related_cards: [{ knowledge_id: "kc_two", kind: "project", title: "同源项目", deep_link: "/r/knowledge/kc_two" }],
  });
  render(<App />);
  expect(await screen.findByRole("heading", { name: "课程生意" })).toBeInTheDocument();
  expect(screen.getByText("有冲突证据")).toBeInTheDocument();
  expect(screen.getByText("未知")).toBeInTheDocument();
  expect(screen.getByText("原文收入证据")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "调研状态：继续观察" })).toBeInTheDocument();
  expect(screen.getByText("用户选择")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "原帖" })).toHaveAttribute("href", "/r/card/one");
  expect(screen.getByRole("link", { name: "同源项目" })).toHaveAttribute("href", "/r/knowledge/kc_two");
  expect([...document.querySelectorAll(".knowledge-reader__fields > div > dt")].map(el => el.textContent)).toEqual(["生意", "价格", "获客渠道", "商业模式"]);
});

it("shows a missing or unavailable knowledge card", async () => {
  vi.mocked(getJson).mockRejectedValue(new Error("missing"));
  render(<App />);
  expect(await screen.findByRole("heading", { name: "知识卡不存在或暂时无法读取" })).toBeInTheDocument();
});
