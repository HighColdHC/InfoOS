// useConfirm — promise-based confirm dialog.
//
// Returns a function that, when called, opens a confirmation modal and
// resolves to true (confirmed) or false (cancelled). The dialog is
// promise-based so it fits existing async handlers without prop
// drilling. State lives in a single provider near the toast/root.
//
// We render the dialog using the shared <Modal> primitive so we get
// Escape/click-outside for free.

import { createContext, createElement, useCallback, useContext, useMemo, useRef, useState } from "react";
import type { ReactElement, ReactNode } from "react";
import { Modal } from "../Modal";

export type ConfirmOptions = {
  title: string;
  body?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  /** When true, the confirm button uses the danger color palette. */
  danger?: boolean;
};

export type ConfirmContextValue = {
  confirm: (options: ConfirmOptions) => Promise<boolean>;
};

const ConfirmContext = createContext<ConfirmContextValue | null>(null);

type PendingConfirm = ConfirmOptions & { resolve: (value: boolean) => void };

export function ConfirmProvider({ children }: { children: ReactNode }): ReactElement {
  const [pending, setPending] = useState<PendingConfirm | null>(null);
  // Hold the resolver outside of state so closing the dialog doesn't
  // re-render with a stale function.
  const resolver = useRef<((value: boolean) => void) | null>(null);

  const confirm = useCallback((options: ConfirmOptions): Promise<boolean> => {
    return new Promise<boolean>((resolve) => {
      resolver.current = resolve;
      // Cast through unknown — the runtime shape matches, but TS
      // can't see that we add a private ``resolve`` field here.
      setPending(options as unknown as PendingConfirm);
    });
  }, []);

  const close = useCallback((result: boolean) => {
    if (resolver.current) {
      resolver.current(result);
      resolver.current = null;
    }
    setPending(null);
  }, []);

  const value = useMemo<ConfirmContextValue>(() => ({ confirm }), [confirm]);

  return createElement(
    ConfirmContext.Provider,
    { value },
    children,
    pending
      ? createElement(
          Modal,
          {
            open: true,
            onClose: () => close(false),
            title: pending.title,
            width: 460,
            children: pending.body ?? null,
            actions: createElement(
              "fragment",
              {},
              createElement(
                "button",
                {
                  type: "button",
                  className: pending.danger ? "text-button primary danger" : "text-button primary",
                  onClick: () => close(true),
                },
                pending.confirmLabel ?? "确认",
              ),
              createElement(
                "button",
                { type: "button", className: "text-button", onClick: () => close(false) },
                pending.cancelLabel ?? "取消",
              ),
            ),
          },
        )
      : null,
  );
}

export function useConfirm(): ConfirmContextValue["confirm"] {
  const value = useContext(ConfirmContext);
  if (!value) {
    // No provider — resolve as true so tests / unmounted callers don't
    // silently no-op confirmation. Production code should always be
    // wrapped in <ConfirmProvider>.
    return async () => true;
  }
  return value.confirm;
}
