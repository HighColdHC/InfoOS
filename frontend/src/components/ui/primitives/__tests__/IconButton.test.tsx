import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Trash2 } from "lucide-react";
import { IconButton } from "../IconButton";

describe("IconButton", () => {
  it("requires aria-label", () => {
    // @ts-expect-error aria-label missing
    render(<IconButton icon={Trash2} />);
    // ts-expect-error above verifies the type signature
  });

  it("renders icon with aria-label", () => {
    render(<IconButton icon={Trash2} aria-label="Delete" onClick={() => {}} />);
    expect(screen.getByRole("button", { name: "Delete" })).toBeInTheDocument();
  });

  it("applies danger variant class", () => {
    render(<IconButton icon={Trash2} aria-label="Delete" variant="danger" />);
    expect(screen.getByRole("button")).toHaveClass("icon-btn--danger");
  });

  it("invokes onClick", () => {
    const onClick = vi.fn();
    render(<IconButton icon={Trash2} aria-label="Delete" onClick={onClick} />);
    fireEvent.click(screen.getByRole("button"));
    expect(onClick).toHaveBeenCalledOnce();
  });
});
