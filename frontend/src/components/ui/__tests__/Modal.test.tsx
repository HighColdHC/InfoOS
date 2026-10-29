// Modal tests — portal, Escape, backdrop click, focus management.

import { useState } from "react";
import { render, screen, fireEvent, act, waitFor } from "@testing-library/react";
import { Modal } from "../Modal";

describe("Modal", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  test("does not render when closed", () => {
    render(
      <Modal open={false} onClose={() => {}} title="t">
        content
      </Modal>
    );
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  test("renders children and title when open", () => {
    render(
      <Modal open={true} onClose={() => {}} title="My Modal">
        <p>body text</p>
      </Modal>
    );
    expect(screen.getByRole("dialog", { name: "My Modal" })).toBeInTheDocument();
    expect(screen.getByText("body text")).toBeInTheDocument();
  });

  test("Escape calls onClose", () => {
    const onClose = vi.fn();
    render(
      <Modal open={true} onClose={onClose} title="t">
        <button>x</button>
      </Modal>
    );
    act(() => {
      fireEvent.keyDown(document, { key: "Escape" });
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  test("Escape closes only the top modal and restores its opener and nested scroll lock", async () => {
    function Nested() {
      const [open, setOpen] = useState(false);
      const [inner, setInner] = useState(false);
      return <>
        <button onClick={() => setOpen(true)}>打开外层</button>
        <Modal open={open} onClose={() => setOpen(false)} title="外层">
          <button onClick={() => setInner(true)}>打开内层</button>
        </Modal>
        <Modal open={inner} onClose={() => setInner(false)} title="内层">正文</Modal>
      </>;
    }
    const overflow = document.body.style.overflow;
    render(<Nested />);
    const outerOpener = screen.getByRole("button", { name: "打开外层" });
    outerOpener.focus();
    fireEvent.click(outerOpener);
    await waitFor(() => expect(screen.getByRole("dialog", { name: "外层" })).toContainElement(document.activeElement as HTMLElement));
    const innerOpener = screen.getByRole("button", { name: "打开内层" });
    innerOpener.focus();
    fireEvent.click(innerOpener);
    const inner = screen.getByRole("dialog", { name: "内层" });
    await waitFor(() => expect(inner).toContainElement(document.activeElement as HTMLElement));
    fireEvent.keyDown(inner, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "内层" })).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "外层" })).toBeInTheDocument();
    expect(innerOpener).toHaveFocus();
    expect(document.body.style.overflow).toBe("hidden");
    fireEvent.keyDown(innerOpener, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(outerOpener).toHaveFocus();
    expect(document.body.style.overflow).toBe(overflow);
  });

  test("clicking the backdrop calls onClose", () => {
    const onClose = vi.fn();
    render(
      <Modal open={true} onClose={onClose} title="t">
        <p>body</p>
      </Modal>
    );
    const dialog = screen.getByRole("dialog");
    // The click event target is the dialog itself when clicking the backdrop.
    act(() => {
      fireEvent.click(dialog, { target: dialog });
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  test("clicking inside the card does NOT call onClose", () => {
    const onClose = vi.fn();
    render(
      <Modal open={true} onClose={onClose} title="t">
        <p>body</p>
      </Modal>
    );
    const body = screen.getByText("body");
    act(() => {
      // Click on a child of the dialog (not the dialog itself).
      fireEvent.click(body, { bubbles: true });
    });
    expect(onClose).not.toHaveBeenCalled();
  });

  test("respects busy: Escape is a no-op when busy", () => {
    const onClose = vi.fn();
    render(
      <Modal open={true} onClose={onClose} title="t" busy={true}>
        <p>body</p>
      </Modal>
    );
    act(() => {
      fireEvent.keyDown(document, { key: "Escape" });
    });
    expect(onClose).not.toHaveBeenCalled();
  });

  test("ariaLabel overrides the title for screen readers", () => {
    render(
      <Modal open={true} onClose={() => {}} title="视觉标题" ariaLabel="可访问名">
        <p>body</p>
      </Modal>
    );
    expect(screen.getByRole("dialog", { name: "可访问名" })).toBeInTheDocument();
  });

  test("renders actions footer when provided", () => {
    render(
      <Modal
        open={true}
        onClose={() => {}}
        title="t"
        actions={<button type="button">Action</button>}
      >
        <p>body</p>
      </Modal>
    );
    expect(screen.getByRole("button", { name: "Action" })).toBeInTheDocument();
  });

  test("caps an explicit width by the backdrop's two viewport gutters", () => {
    render(<Modal open={true} onClose={() => {}} title="narrow" width={1180}>body</Modal>);
    const card = screen.getByRole("dialog").querySelector(".modal-card") as HTMLElement;
    expect(card.style.width).toBe("1180px");
    expect(card.style.maxWidth).toBe("calc(100vw - (2 * var(--space-xl)))");
  });
});
