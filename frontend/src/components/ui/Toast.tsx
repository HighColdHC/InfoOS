// useToast — hook for pushing transient notifications.
//
// Replaces the seven scattered useState<string | null> status fields
// that used to live in App.tsx (savingStatus, prepareStatus,
// monitorStatus, actionStatus, etc.). The single global queue
// serializes messages naturally and fixes the "savingStatus
// clobbering" problem where one async action's success message would
// overwrite another's error.

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactElement, ReactNode } from "react";

export type ToastKind = "info" | "success" | "warning" | "error";

export type ToastInput = {
  kind: ToastKind;
  message: string;
  /** Auto-dismiss delay in ms. Pass 0 to require manual dismiss. */
  durationMs?: number;
};

export type ToastEntry = {
  id: string;
  kind: ToastKind;
  message: string;
  durationMs: number;
};

export type ToastContextValue = {
  pushToast: (input: ToastInput) => string;
  dismiss: (id: string) => void;
};

export const ToastContext = createContext<ToastContextValue | null>(null);

const DEFAULT_DURATION_MS = 4000;
const MAX_VISIBLE = 5;

let _idCounter = 0;
function nextId(): string {
  _idCounter += 1;
  return `t-${Date.now().toString(36)}-${_idCounter}`;
}

export function ToastProvider({ children }: { children: ReactNode }): ReactElement {
  const [toasts, setToasts] = useState<ToastEntry[]>([]);
  const timers = useRef<Map<string, number>>(new Map());

  const dismiss = useCallback((id: string) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
    const timerId = timers.current.get(id);
    if (timerId !== undefined) {
      window.clearTimeout(timerId);
      timers.current.delete(id);
    }
  }, []);

  const pushToast = useCallback(
    (input: ToastInput): string => {
      const id = nextId();
      const entry: ToastEntry = {
        id,
        kind: input.kind,
        message: input.message,
        durationMs: input.durationMs ?? DEFAULT_DURATION_MS,
      };
      setToasts((current) => {
        const next = [...current, entry];
        // Cap visible toasts; oldest get dropped.
        if (next.length > MAX_VISIBLE) {
          return next.slice(next.length - MAX_VISIBLE);
        }
        return next;
      });
      if (entry.durationMs > 0) {
        const timerId = window.setTimeout(() => {
          dismiss(id);
        }, entry.durationMs);
        timers.current.set(id, timerId);
      }
      return id;
    },
    [dismiss],
  );

  useEffect(() => {
    // Cleanup all timers on unmount.
    const map = timers.current;
    return () => {
      map.forEach((id) => window.clearTimeout(id));
      map.clear();
    };
  }, []);

  const value = useMemo<ToastContextValue>(
    () => ({ pushToast, dismiss }),
    [pushToast, dismiss],
  );

  return (
    <ToastContext.Provider value={value}>
      {children}
      <ToastViewport toasts={toasts} onDismiss={dismiss} />
    </ToastContext.Provider>
  );
}

function ToastViewport({
  toasts,
  onDismiss,
}: {
  toasts: ToastEntry[];
  onDismiss: (id: string) => void;
}): ReactElement | null {
  if (toasts.length === 0) {
    return null;
  }
  return (
    <div className="toast-stack" role="region" aria-live="polite" aria-label="通知">
      {toasts.map((toast) => (
        <div key={toast.id} className={`toast toast-${toast.kind}`} role="status">
          <span className="toast-message">{toast.message}</span>
          <button
            type="button"
            className="toast-dismiss btn btn--ghost btn--sm"
            onClick={() => onDismiss(toast.id)}
            aria-label="关闭通知"
          >
            ×
          </button>
        </div>
      ))}
    </div>
  );
}

export function useToast(): ToastContextValue {
  const value = useContext(ToastContext);
  if (!value) {
    // Fallback no-op so isolated tests / unmounted callers don't crash.
    return {
      pushToast: () => "",
      dismiss: () => {
        /* no-op */
      },
    };
  }
  return value;
}
