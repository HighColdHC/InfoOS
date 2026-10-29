// Toast tests — push, auto-dismiss, manual dismiss, max visible cap.

import { act, fireEvent, render, screen } from "@testing-library/react";
import { vi } from "vitest";
import { ToastProvider, useToast } from "../Toast";

function ToastProbe() {
  const toast = useToast();
  return (
    <div>
      <button
        type="button"
        onClick={() =>
          toast.pushToast({ kind: "info", message: "hello", durationMs: 0 })
        }
      >
        push
      </button>
      <button
        type="button"
        onClick={() =>
          toast.pushToast({ kind: "success", message: "ok", durationMs: 0 })
        }
      >
        push-success
      </button>
      <button
        type="button"
        onClick={() => toast.pushToast({ kind: "error", message: "boom", durationMs: 0 })}
      >
        push-error
      </button>
      <button
        type="button"
        onClick={() => toast.pushToast({ kind: "info", message: "auto", durationMs: 2000 })}
      >
        push-auto
      </button>
    </div>
  );
}

describe("ToastProvider", () => {
  test("pushToast renders a toast", () => {
    render(
      <ToastProvider>
        <ToastProbe />
      </ToastProvider>
    );
    act(() => {
      fireEvent.click(screen.getByText("push"));
    });
    expect(screen.getByText("hello")).toBeInTheDocument();
  });

  test("multiple toasts stack and are all visible", () => {
    render(
      <ToastProvider>
        <ToastProbe />
      </ToastProvider>
    );
    act(() => {
      fireEvent.click(screen.getByText("push"));
      fireEvent.click(screen.getByText("push-success"));
    });
    expect(screen.getByText("hello")).toBeInTheDocument();
    expect(screen.getByText("ok")).toBeInTheDocument();
  });

  test("kind classes are applied to each toast", () => {
    render(
      <ToastProvider>
        <ToastProbe />
      </ToastProvider>
    );
    act(() => {
      fireEvent.click(screen.getByText("push-error"));
    });
    const toastEl = screen.getByText("boom").closest(".toast");
    expect(toastEl).toHaveClass("toast-error");
  });

  test("manual dismiss button removes the toast", () => {
    render(
      <ToastProvider>
        <ToastProbe />
      </ToastProvider>
    );
    act(() => {
      fireEvent.click(screen.getByText("push"));
    });
    expect(screen.getByText("hello")).toBeInTheDocument();
    act(() => {
      fireEvent.click(screen.getByLabelText("关闭通知"));
    });
    expect(screen.queryByText("hello")).toBeNull();
  });

  test("toasts auto-dismiss after durationMs (with fake timers)", () => {
    vi.useFakeTimers();
    try {
      render(
        <ToastProvider>
          <ToastProbe />
        </ToastProvider>
      );
      act(() => {
        fireEvent.click(screen.getByText("push-auto"));
      });
      expect(screen.getByText("auto")).toBeInTheDocument();
      act(() => {
        vi.advanceTimersByTime(3000);
      });
      expect(screen.queryByText("auto")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});
