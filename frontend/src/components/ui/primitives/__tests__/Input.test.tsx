import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Input } from "../Input";

describe("Input", () => {
  it("renders label and input with generated id linkage", () => {
    render(<Input label="Email" />);
    const input = screen.getByLabelText("Email");
    expect(input).toBeInTheDocument();
    expect(input.tagName).toBe("INPUT");
  });

  it("renders required asterisk when required", () => {
    render(<Input label="Email" required />);
    expect(screen.getByText("Email").className).toContain("input-field__label--required");
  });

  it("renders error state with role=alert", () => {
    render(<Input label="Email" error="Invalid email" />);
    expect(screen.getByRole("alert")).toHaveTextContent("Invalid email");
    // The --error class lives on the wrapper <div>, not the <input> element.
    const input = screen.getByLabelText("Email");
    expect(input.parentElement).toHaveClass("input-field--error");
  });

  it("renders hint when no error", () => {
    render(<Input label="Email" hint="We'll never share" />);
    expect(screen.getByText("We'll never share")).toHaveClass("input-field__hint");
  });

  it("renders textarea when textarea prop is set", () => {
    render(<Input label="Note" textarea value="hello" onChange={() => {}} />);
    const ta = screen.getByLabelText("Note");
    expect(ta.tagName).toBe("TEXTAREA");
  });

  it("calls onChange when typed", () => {
    const onChange = vi.fn();
    render(<Input label="Email" onChange={onChange} />);
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "a@b" } });
    expect(onChange).toHaveBeenCalledOnce();
  });
});
