import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ExpandableText } from "../ExpandableText";

describe("ExpandableText", () => {
  it("renders short text directly without a button", () => {
    render(<ExpandableText text="hi" charLimit={500} />);
    expect(screen.getByText("hi")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("renders exactly at the limit without a button (boundary)", () => {
    const text = "a".repeat(500);
    render(<ExpandableText text={text} charLimit={500} />);
    expect(screen.getByText(text)).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("renders long text collapsed with truncation marker and 展开 button", () => {
    const long = "b".repeat(600);
    render(<ExpandableText text={long} charLimit={500} />);
    const truncated = "b".repeat(500) + "…";
    expect(screen.getByText(truncated)).toBeInTheDocument();
    expect(screen.queryByText(long)).not.toBeInTheDocument();
    const button = screen.getByRole("button", { name: "展开" });
    expect(button).toHaveClass("expand-link");
  });

  it("expands to full text and shows 收起 button when 展开 is clicked", () => {
    const long = "c".repeat(600);
    render(<ExpandableText text={long} charLimit={500} />);
    fireEvent.click(screen.getByRole("button", { name: "展开" }));
    expect(screen.getByText(long)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "收起" })).toBeInTheDocument();
  });

  it("collapses back to truncation when 收起 is clicked", () => {
    const long = "d".repeat(600);
    render(<ExpandableText text={long} charLimit={500} />);
    fireEvent.click(screen.getByRole("button", { name: "展开" }));
    fireEvent.click(screen.getByRole("button", { name: "收起" }));
    expect(screen.getByText("d".repeat(500) + "…")).toBeInTheDocument();
    expect(screen.queryByText(long)).not.toBeInTheDocument();
  });

  it("treats charLimit=0 as always long (any non-empty text is truncated)", () => {
    render(<ExpandableText text="short" charLimit={0} />);
    expect(screen.getByText("…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "展开" })).toBeInTheDocument();
  });
});
