import { render, screen } from "@testing-library/react";
import { Sidebar, SIDEBAR_MENUS } from "../Sidebar";

const counts = { cards: 5, sources: 1, tasks: 3 };

describe("Sidebar badges", () => {
  it("renders badges for entries with count > 0", () => {
    render(<Sidebar activeKey="cards" onSelect={vi.fn()} badgeCounts={counts} />);
    expect(screen.getByText("5")).toBeInTheDocument();
    expect(screen.getByText("1")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("does not render badge when count is 0", () => {
    render(
      <Sidebar activeKey="cards" onSelect={vi.fn()} badgeCounts={counts} />
    );
    // Badge primitive returns null when count is 0, so no "0" text is rendered.
    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });

  it("shows 99+ for counts > 99", () => {
    render(
      <Sidebar activeKey="cards" onSelect={vi.fn()} badgeCounts={{ ...counts, tasks: 150 }} />
    );
    expect(screen.getByText("99+")).toBeInTheDocument();
  });
});

describe("Sidebar menu entries", () => {
  it("includes the new InfoOS product surfaces", () => {
    expect(SIDEBAR_MENUS.map((m) => m.key)).toEqual(["cards", "sources", "tasks", "environment", "tokens", "account", "logs"]);
    expect(SIDEBAR_MENUS.map((m) => m.label)).toEqual(["信息卡", "信息源", "采集任务", "采集环境", "Token 认证", "账号与安全", "导出与日志"]);
  });

  it("does not expose the old intelligence-workbench menus", () => {
    for (const key of ["today", "signals", "decisions", "studio", "system", "llm"]) {
      expect(SIDEBAR_MENUS.find((menu) => menu.key === key)).toBeUndefined();
    }
  });
});
