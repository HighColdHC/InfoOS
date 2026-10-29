import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { SetupCard } from "../SetupCard";

describe("SetupCard", () => {
  it("renders title, status badge, and primary action label", () => {
    render(
      <SetupCard
        step={1}
        title="Launch InfoOS Chrome"
        status="pending"
        primaryLabel="启动专用 Chrome"
        onPrimary={() => {}}
      />
    );
    expect(screen.getByText("步骤 1")).toBeTruthy();
    expect(screen.getByText("Launch InfoOS Chrome")).toBeTruthy();
    expect(screen.getByText("启动专用 Chrome")).toBeTruthy();
  });

  it("invokes onPrimary when the primary button is clicked", () => {
    const onPrimary = vi.fn();
    render(
      <SetupCard step={1} title="x" status="pending" primaryLabel="Go" onPrimary={onPrimary} />
    );
    fireEvent.click(screen.getByText("Go"));
    expect(onPrimary).toHaveBeenCalledOnce();
  });

  it("renders an error message when provided", () => {
    render(
      <SetupCard
        step={2}
        title="x"
        status="failed"
        primaryLabel="Retry"
        onPrimary={() => {}}
        error="boom"
      />
    );
    expect(screen.getByText("boom")).toBeTruthy();
  });
});
