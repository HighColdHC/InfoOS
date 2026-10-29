// Hook tests — useConfirm, useHotkeys.

import { act, fireEvent, render, screen } from "@testing-library/react";
import { useConfirm, ConfirmProvider } from "../hooks/useConfirm";
import { useHotkeys } from "../hooks/useHotkeys";
import { vi } from "vitest";

function ConfirmProbe({ onResult }: { onResult: (v: boolean) => void }) {
  const confirm = useConfirm();
  return (
    <button
      type="button"
      onClick={async () => {
        const v = await confirm({
          title: "t",
          body: "b",
          confirmLabel: "Yes",
          danger: true,
        });
        onResult(v);
      }}
    >
      ask
    </button>
  );
}

describe("useConfirm", () => {
  test("resolves true on confirm and false on cancel", async () => {
    const onResult = vi.fn();
    render(
      <ConfirmProvider>
        <ConfirmProbe onResult={onResult} />
      </ConfirmProvider>
    );
    act(() => {
      fireEvent.click(screen.getByText("ask"));
    });
    // Modal renders.
    expect(screen.getByRole("dialog", { name: "t" })).toBeInTheDocument();
    // Click "Yes".
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Yes" }));
    });
    expect(onResult).toHaveBeenCalledWith(true);
  });

  test("cancel via Escape resolves false", async () => {
    const onResult = vi.fn();
    render(
      <ConfirmProvider>
        <ConfirmProbe onResult={onResult} />
      </ConfirmProvider>
    );
    act(() => {
      fireEvent.click(screen.getByText("ask"));
    });
    await act(async () => {
      fireEvent.keyDown(document, { key: "Escape" });
    });
    expect(onResult).toHaveBeenCalledWith(false);
  });
});

function HotkeyProbe({ onFire }: { onFire: (label: string) => void }) {
  useHotkeys({
    "g b": () => onFire("go-brief"),
    "/": () => onFire("focus"),
    "?": () => onFire("help"),
  });
  return <input type="search" aria-label="search" />;
}

describe("useHotkeys", () => {
  test("single key binding fires its handler", () => {
    const onFire = vi.fn();
    render(<HotkeyProbe onFire={onFire} />);
    act(() => {
      fireEvent.keyDown(document, { key: "?" });
    });
    expect(onFire).toHaveBeenCalledWith("help");
  });

  test("two-key sequence fires after both keys arrive", () => {
    const onFire = vi.fn();
    render(<HotkeyProbe onFire={onFire} />);
    act(() => {
      fireEvent.keyDown(document, { key: "g" });
      fireEvent.keyDown(document, { key: "b" });
    });
    expect(onFire).toHaveBeenCalledWith("go-brief");
  });

  test("two-key sequence does not fire if second key never arrives", () => {
    const onFire = vi.fn();
    render(<HotkeyProbe onFire={onFire} />);
    act(() => {
      fireEvent.keyDown(document, { key: "g" });
    });
    expect(onFire).not.toHaveBeenCalled();
  });

  test("does not fire when focus is in an editable input", () => {
    const onFire = vi.fn();
    render(<HotkeyProbe onFire={onFire} />);
    const input = screen.getByLabelText("search");
    input.focus();
    act(() => {
      fireEvent.keyDown(input, { key: "?" });
    });
    expect(onFire).not.toHaveBeenCalled();
  });
});
