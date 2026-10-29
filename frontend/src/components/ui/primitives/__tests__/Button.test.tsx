import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Star } from "lucide-react";
import { Button } from "../Button";

describe("Button", () => {
  it("renders default secondary button with children", () => {
    render(<Button>Click me</Button>);
    const btn = screen.getByRole("button", { name: "Click me" });
    expect(btn).toHaveClass("btn", "btn--secondary", "btn--md");
  });

  it("applies variant and size classes", () => {
    render(<Button variant="primary" size="lg">Big primary</Button>);
    expect(screen.getByRole("button")).toHaveClass("btn--primary", "btn--lg");
  });

  it("renders an icon before the label", () => {
    render(<Button icon={Star}>Starred</Button>);
    const btn = screen.getByRole("button");
    expect(btn.querySelector(".btn__icon")).toBeInTheDocument();
    expect(btn.textContent).toMatch(/Starred/);
  });

  it("renders loading state with aria-busy and disabled", () => {
    render(<Button loading>Saving</Button>);
    const btn = screen.getByRole("button");
    expect(btn).toHaveAttribute("aria-busy", "true");
    expect(btn).toBeDisabled();
  });

  it("calls onClick when clicked", () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick}>Go</Button>);
    fireEvent.click(screen.getByRole("button"));
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("renders fullWidth modifier", () => {
    render(<Button fullWidth>Full</Button>);
    expect(screen.getByRole("button")).toHaveClass("btn--full");
  });
});
