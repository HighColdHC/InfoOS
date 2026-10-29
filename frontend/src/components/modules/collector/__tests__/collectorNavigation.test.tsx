import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AppShell, type CollectorMenuKey } from "../../../../AppShell";

vi.mock("../../../../lib/api/research", () => ({
  fetchResearchCapabilities: () => new Promise(() => undefined),
  fetchResearchRuns: () => new Promise(() => undefined),
}));

const labels: Record<CollectorMenuKey, string> = {
  cards: "信息卡",
  sources: "信息源",
  tasks: "采集任务",
  environment: "采集环境",
  tokens: "Token 认证",
  account: "账号与安全",
  logs: "导出与日志",
};

describe("collector page mounting", () => {
  for (const [menu, label] of Object.entries(labels) as Array<[CollectorMenuKey, string]>) {
    it(`mounts only the ${menu} module`, () => {
      render(
        <AppShell
          activeMenu={menu}
          onSelectMenu={() => undefined}
          status="sync"
          latestCollectionAt={null}
          onRefresh={() => undefined}
          showOnboarding={false}
          onCloseOnboarding={() => undefined}
          onStartSetup={async () => undefined}
        />,
      );
      expect(screen.getByRole("heading", { name: label })).toBeInTheDocument();
      expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    });
  }
});
