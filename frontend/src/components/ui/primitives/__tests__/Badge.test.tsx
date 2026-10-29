import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Badge } from "../Badge";

describe("Badge", () => {
  it("renders count badge", () => {
    render(<Badge count={5} />);
    expect(screen.getByText("5")).toBeInTheDocument();
  });

  it("renders 99+ for counts above 99", () => {
    render(<Badge count={150} />);
    expect(screen.getByText("99+")).toBeInTheDocument();
  });

  it("does not render for count 0", () => {
    const { container } = render(<Badge count={0} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders dot variant", () => {
    const { container } = render(<Badge variant="dot" />);
    expect(container.querySelector(".badge--dot")).toBeInTheDocument();
  });

  it("renders text variant with children", () => {
    render(<Badge variant="text">Pending</Badge>);
    expect(screen.getByText("Pending")).toBeInTheDocument();
  });
});
