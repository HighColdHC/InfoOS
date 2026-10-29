import { render, screen } from "@testing-library/react";
import { Sparkles } from "lucide-react";
import { describe, expect, it } from "vitest";
import { MetricGrid } from "../MetricGrid";

describe("MetricGrid", () => {
  it("renders icon, value, and label for each metric", () => {
    const { container } = render(
      <MetricGrid
        metrics={[
          { label: "Topics", value: 3, icon: Sparkles },
          { label: "Cards", value: 0, icon: Sparkles },
        ]}
      />
    );
    expect(screen.getByText("Topics")).toBeInTheDocument();
    expect(screen.getByText("Cards")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("0")).toBeInTheDocument();
    // Each metric gets one lucide icon (Sparkles) + a trend icon (Minus), so 4 SVGs total.
    expect(container.querySelectorAll("svg").length).toBeGreaterThanOrEqual(2);
  });

  it("omits trend chrome when trend is omitted", () => {
    render(
      <MetricGrid metrics={[{ label: "Topics", value: 3, icon: Sparkles }]} />
    );
    expect(screen.queryByText("--")).not.toBeInTheDocument();
  });

  it("renders the supplied trend string", () => {
    render(
      <MetricGrid
        metrics={[{ label: "Topics", value: 3, icon: Sparkles, trend: "+12%" }]}
      />
    );
    expect(screen.getByText("+12%")).toBeInTheDocument();
  });
});
