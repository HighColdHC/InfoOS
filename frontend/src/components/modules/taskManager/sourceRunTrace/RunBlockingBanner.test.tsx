import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { BlockedSource } from "../../../../lib/types/sourceRuns";
import { RunBlockingBanner } from "./RunBlockingBanner";

const source = (id: string, reason: string): BlockedSource => ({
  source_id: id, source_name: id, platform: "x", reason,
});

describe("RunBlockingBanner", () => {
  it("将来源不可用明确限定为所列来源并链接到来源管理", () => {
    render(<RunBlockingBanner sources={[source("missing-account", "source_unavailable")]} />);
    expect(screen.getByRole("alert")).toHaveTextContent("所列来源的账号或订阅地址不存在/不可用");
    expect(screen.getByRole("link", { name: "处理 x 来源" })).toHaveAttribute("href", "?menu=sources");
    expect(screen.queryByRole("link", { name: "处理 x 登录" })).not.toBeInTheDocument();
  });

  it("同平台登录故障与来源不可用分别列出，保留各自处理入口", () => {
    render(<RunBlockingBanner sources={[
      source("missing-account", "source_unavailable"),
      source("login-expired", "platform_not_logged_in"),
    ]} />);
    const sourceLine = screen.getByText("missing-account").closest("p");
    expect(sourceLine).toHaveTextContent("所列来源的账号或订阅地址不存在/不可用");
    expect(sourceLine).not.toHaveTextContent("平台登录已失效");
    expect(screen.getByText("login-expired").closest("p")).toHaveTextContent("平台登录已失效");
    expect(screen.getByRole("link", { name: "处理 x 登录" })).toHaveAttribute("href", "?menu=environment&platform=x");
    expect(screen.getByRole("link", { name: "处理 x 来源" })).toBeInTheDocument();
  });
});
