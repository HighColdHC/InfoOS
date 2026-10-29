import { describe, it, expect, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import { useAsyncEffect } from "../useAsyncEffect";

describe("useAsyncEffect", () => {
  it("runs a synchronous effect once on mount", () => {
    const effect = vi.fn();
    renderHook(() => useAsyncEffect(() => effect(), []));
    expect(effect).toHaveBeenCalledTimes(1);
  });

  it("runs an async effect to completion", async () => {
    let resolved = false;
    renderHook(() =>
      useAsyncEffect(
        () =>
          new Promise<void>((resolve) => {
            setTimeout(() => {
              resolved = true;
              resolve();
            }, 10);
          }),
        [],
      ),
    );
    await new Promise((r) => setTimeout(r, 30));
    expect(resolved).toBe(true);
  });

  it("does not call onError after unmount", async () => {
    const onError = vi.fn();
    const { unmount } = renderHook(() =>
      useAsyncEffect(
        () =>
          new Promise<void>((_, reject) => {
            setTimeout(() => reject(new Error("late")), 20);
          }),
        [],
        { onError },
      ),
    );
    unmount();
    await new Promise((r) => setTimeout(r, 40));
    expect(onError).not.toHaveBeenCalled();
  });

  it("re-runs the effect when deps change", () => {
    const effect = vi.fn();
    const { rerender } = renderHook(({ dep }) => useAsyncEffect(() => effect(), [dep]), {
      initialProps: { dep: 1 },
    });
    expect(effect).toHaveBeenCalledTimes(1);
    rerender({ dep: 2 });
    expect(effect).toHaveBeenCalledTimes(2);
    rerender({ dep: 2 });
    expect(effect).toHaveBeenCalledTimes(2);
  });

  it("invokes onError when the effect throws", async () => {
    const onError = vi.fn();
    renderHook(() =>
      useAsyncEffect(
        () => Promise.reject(new Error("boom")),
        [],
        { onError },
      ),
    );
    await new Promise((r) => setTimeout(r, 20));
    expect(onError).toHaveBeenCalledTimes(1);
    expect((onError.mock.calls[0]?.[0] as Error).message).toBe("boom");
  });

  it("logs to console.error when onError is not provided", async () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    renderHook(() => useAsyncEffect(() => Promise.reject(new Error("x")), []));
    await new Promise((r) => setTimeout(r, 20));
    expect(spy).toHaveBeenCalled();
    spy.mockRestore();
  });

  it("silently ignores errors raised after the signal is aborted", async () => {
    const onError = vi.fn();
    const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const { unmount } = renderHook(() =>
      useAsyncEffect(
        (signal) =>
          new Promise<void>((_, reject) => {
            const timer = setTimeout(() => reject(new Error("post-abort")), 20);
            signal.addEventListener("abort", () => {
              clearTimeout(timer);
              reject(new Error("aborted"));
            });
          }),
        [],
        { onError },
      ),
    );
    unmount();
    await new Promise((r) => setTimeout(r, 40));
    expect(onError).not.toHaveBeenCalled();
    expect(consoleSpy).not.toHaveBeenCalled();
    consoleSpy.mockRestore();
  });

  it("passes an AbortSignal to the effect", () => {
    let captured: AbortSignal | null = null;
    renderHook(() =>
      useAsyncEffect((signal) => {
        captured = signal;
      }, []),
    );
    expect(captured).not.toBeNull();
    expect(captured).toBeInstanceOf(AbortSignal);
    expect(captured!.aborted).toBe(false);
  });

  it("aborts the signal on unmount", async () => {
    let captured: AbortSignal | null = null;
    const { unmount } = renderHook(() =>
      useAsyncEffect((signal) => {
        captured = signal;
      }, []),
    );
    const signal = captured!;
    unmount();
    expect(signal.aborted).toBe(true);
  });
});
