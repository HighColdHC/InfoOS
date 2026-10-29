import "@testing-library/jest-dom/vitest";

import { beforeEach, vi } from "vitest";

beforeEach(() => {
  vi.stubGlobal("matchMedia", vi.fn((media: string) => ({
    matches: false,
    media,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })));

  vi.stubGlobal("IntersectionObserver", class {
    observe() {}
    unobserve() {}
    disconnect() {}
  });
  HTMLElement.prototype.scrollIntoView = vi.fn();

  vi.stubGlobal("requestIdleCallback", vi.fn(() => 0));
  vi.stubGlobal("cancelIdleCallback", vi.fn());
});
