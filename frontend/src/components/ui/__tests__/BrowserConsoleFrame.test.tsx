import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BrowserConsoleFrame } from "../BrowserConsoleFrame";


describe("BrowserConsoleFrame", () => {
  afterEach(() => vi.useRealTimers());

  it("loads only the supplied same-origin path and expires an idle console", () => {
    vi.useFakeTimers();
    const onExpired = vi.fn();
    render(<BrowserConsoleFrame url="/browser-console/" onExpired={onExpired} idleMs={1000} absoluteMs={5000} />);

    expect(screen.getByTitle("InfoOS 容器浏览器")).toHaveAttribute("src", "/browser-console/");
    act(() => vi.advanceTimersByTime(1001));
    expect(onExpired).toHaveBeenCalledTimes(1);
  });

  it("enforces an absolute deadline independently of the idle timer", () => {
    vi.useFakeTimers();
    const onExpired = vi.fn();
    render(<BrowserConsoleFrame url="/browser-console/" onExpired={onExpired} idleMs={10_000} absoluteMs={2500} />);

    act(() => vi.advanceTimersByTime(2501));
    expect(onExpired).toHaveBeenCalled();
  });
});
