import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { SkeletonBlock } from "../SkeletonBlock";

describe("SkeletonBlock", () => {
  it("renders 3 skeleton lines by default", () => {
    const { container } = render(<SkeletonBlock />);
    const lines = container.querySelectorAll(".skeleton-line");
    expect(lines).toHaveLength(3);
  });

  it("renders the requested count of skeleton lines", () => {
    const { container } = render(<SkeletonBlock count={5} />);
    expect(container.querySelectorAll(".skeleton-line")).toHaveLength(5);
  });

  it("marks the last line as short", () => {
    const { container } = render(<SkeletonBlock count={3} />);
    const lines = container.querySelectorAll(".skeleton-line");
    expect(lines[0].className).toBe("skeleton-line");
    expect(lines[1].className).toBe("skeleton-line");
    expect(lines[2].className).toBe("skeleton-line short");
  });

  it("forwards label to aria-label and renders role=status", () => {
    render(<SkeletonBlock label="Loading content" />);
    const region = screen.getByRole("status", { name: "Loading content" });
    expect(region).toBeInTheDocument();
  });

  it("passes through className to the parent container", () => {
    const { container } = render(<SkeletonBlock className="extra-class" />);
    const parent = container.querySelector(".content-block-skeleton");
    expect(parent).toHaveClass("extra-class");
  });
});
