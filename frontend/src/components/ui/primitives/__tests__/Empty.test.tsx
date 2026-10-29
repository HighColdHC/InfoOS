import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Empty } from "../Empty";

describe("Empty (primitive re-export)", () => {
  it("renders text-only", () => {
    render(<Empty text="No items" />);
    expect(screen.getByText("No items")).toBeInTheDocument();
  });

  it("renders illustration variant and text", () => {
    const { container } = render(<Empty text="No data" illustration="brief" />);
    expect(screen.getByText("No data")).toBeInTheDocument();
    // The SVG (from the existing Empty component) is rendered without
    // role="img" by default. Target by tag selector.
    expect(container.querySelector("svg")).toBeInTheDocument();
  });

  it("renders CTA button and calls onClick", () => {
    const onClick = vi.fn();
    render(<Empty text="No data" cta={{ label: "Add source", onClick }} />);
    fireEvent.click(screen.getByRole("button", { name: "Add source" }));
    expect(onClick).toHaveBeenCalledOnce();
  });
});
