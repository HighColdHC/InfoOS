import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MediaGapNotice } from "../MediaGapNotice";

describe("MediaGapNotice", () => {
  it.each([false, true])("shows the reason and platform link with partial media: %s", (hasAttachments) => {
    render(<MediaGapNotice gap={{ reason: "douyin_browser_identity_mismatch", status: "blocked" }}
      hasAttachments={hasAttachments} sourceUrl="https://www.douyin.com/video/123" />);
    expect(screen.getByRole("note")).toHaveTextContent(`${hasAttachments ? "部分媒体未下载" : "媒体未下载"} · 打开的页面与卡片不一致`);
    const link = screen.getByRole("link", { name: "去原平台查看" });
    expect(link).toHaveAttribute("href", "https://www.douyin.com/video/123");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noreferrer");
  });

  it("renders nothing without a gap", () => {
    const { container } = render(<MediaGapNotice gap={null} hasAttachments={false} sourceUrl="https://example.test" />);
    expect(container).toBeEmptyDOMElement();
  });
});
