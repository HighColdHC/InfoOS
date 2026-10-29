import { act, render } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import ManagementApp from "./ManagementApp";

const loadCollectorStatus = vi.fn().mockResolvedValue({
  service_status: "ready", latest_collection_at: null, reasons: [],
});

vi.mock("./lib/api/collectorStatus", () => ({ loadCollectorStatus: () => loadCollectorStatus() }));
vi.mock("./lib/api", () => ({ loadSetupState: vi.fn().mockResolvedValue({ current_step: "complete" }), postSetupReset: vi.fn() }));
vi.mock("./AppShell", () => ({ AppShell: () => null, COLLECTOR_MENU_KEYS: ["cards"] }));

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  loadCollectorStatus.mockClear();
});

it("pauses status polling while hidden and refreshes on return", async () => {
  vi.useFakeTimers();
  let visibility: DocumentVisibilityState = "hidden";
  vi.spyOn(document, "visibilityState", "get").mockImplementation(() => visibility);
  const { unmount } = render(<ManagementApp refreshIntervalMs={100} />);
  await act(async () => {});
  expect(loadCollectorStatus).toHaveBeenCalledTimes(1);

  await act(async () => { vi.advanceTimersByTime(300); });
  expect(loadCollectorStatus).toHaveBeenCalledTimes(1);
  visibility = "visible";
  await act(async () => { document.dispatchEvent(new Event("visibilitychange")); });
  expect(loadCollectorStatus).toHaveBeenCalledTimes(2);
  unmount();
  await act(async () => {
    document.dispatchEvent(new Event("visibilitychange"));
    vi.advanceTimersByTime(100);
  });
  expect(loadCollectorStatus).toHaveBeenCalledTimes(2);
});
