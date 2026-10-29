// Modal — accessible dialog with portal, focus trap, Escape, and
// click-outside-to-close behavior. Replaces the four ad-hoc
// modal-backdrop > modal-card blocks that used to live in App.tsx.
//
// The opener's focus is restored on close so keyboard users land back
// where they were before opening.

import { useEffect, useRef, type MouseEvent, type ReactElement, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";

export type ModalProps = {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
  actions?: ReactNode;
  headerActions?: ReactNode;
  width?: number | string;
  size?: "default" | "fullscreen";
  busy?: boolean;
  closeOnBackdrop?: boolean;
  /** Override the accessible name. By default the dialog is named by
   * its title. Set this when the title is a complex ReactNode (e.g.
   * contains badges) and a flat string is more useful for assistive
   * tech and end-to-end tests. */
  ariaLabel?: string;
};

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "textarea:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "summary",
  "video[controls]",
  "audio[controls]",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

const modalStack: symbol[] = [];
let bodyOverflowBeforeModals = "";

function getFocusable(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
    (el) => !el.hasAttribute("aria-hidden") && el.offsetParent !== null,
  );
}

export function Modal({
  open,
  onClose,
  title,
  children,
  actions,
  headerActions,
  width,
  size = "default",
  busy = false,
  closeOnBackdrop = true,
  ariaLabel,
}: ModalProps): ReactElement | null {
  const cardRef = useRef<HTMLDivElement | null>(null);
  const modalId = useRef(Symbol("modal")).current;
  const keyboardOptions = useRef({ onClose, busy });
  const titleId = useRef(`modal-title-${Math.random().toString(36).slice(2, 9)}`).current;

  useEffect(() => {
    keyboardOptions.current = { onClose, busy };
  }, [onClose, busy]);

  useEffect(() => {
    if (!open) {
      return;
    }
    const previouslyFocused = document.activeElement as HTMLElement | null;
    if (modalStack.length === 0) {
      bodyOverflowBeforeModals = document.body.style.overflow;
      document.body.style.overflow = "hidden";
    }
    modalStack.push(modalId);

    function onKey(event: KeyboardEvent) {
      if (modalStack.at(-1) !== modalId || (event.key !== "Escape" && event.key !== "Tab")) return;
      event.stopPropagation();
      if (event.key === "Escape") {
        if (keyboardOptions.current.busy) {
          return; // refuse to close mid-save
        }
        event.preventDefault();
        keyboardOptions.current.onClose();
        return;
      }
      const card = cardRef.current;
      if (!card) {
        return;
      }
      const focusable = getFocusable(card);
      if (focusable.length === 0) {
        // Nothing to tab through — keep focus on the card itself.
        event.preventDefault();
        card.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement as HTMLElement | null;
      if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", onKey, true);
    // Focus the first focusable element on open (or the card itself).
    const id = window.setTimeout(() => {
      const focusTarget = cardRef.current;
      if (!focusTarget || modalStack.at(-1) !== modalId) return;
      const focusable = getFocusable(focusTarget);
      const target = focusable[0] ?? focusTarget;
      target.focus();
    }, 0);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      window.clearTimeout(id);
      const wasTop = modalStack.at(-1) === modalId;
      modalStack.splice(modalStack.indexOf(modalId), 1);
      if (modalStack.length === 0) document.body.style.overflow = bodyOverflowBeforeModals;
      if (wasTop && previouslyFocused) previouslyFocused.focus();
    };
  }, [open, modalId]);

  if (!open) {
    return null;
  }

  function onBackdropClick(event: MouseEvent<HTMLDivElement>) {
    if (!closeOnBackdrop || busy) {
      return;
    }
    if (event.target === event.currentTarget) {
      onClose();
    }
  }

  const cardStyle: { width?: number | string; maxWidth?: string } = {};
  if (size === "fullscreen") {
    // Leave sizing to the fullscreen CSS variant.
  } else if (width !== undefined) {
    cardStyle.width = width;
    // Percentage max-width does not reliably shrink an explicit-width grid
    // item. Match the backdrop's two horizontal gutters explicitly.
    cardStyle.maxWidth = "calc(100vw - (2 * var(--space-xl)))";
  } else {
    cardStyle.maxWidth = "var(--modal-width, 960px)";
  }

  return createPortal(
    <div
      className={`modal-backdrop${size === "fullscreen" ? " modal-backdrop--fullscreen" : ""}`}
      role="dialog"
      aria-modal="true"
      aria-label={ariaLabel}
      aria-labelledby={ariaLabel ? undefined : titleId}
      onClick={onBackdropClick}
    >
      <div
        ref={cardRef}
        className="modal-card"
        tabIndex={-1}
        style={cardStyle}
      >
        <header className="modal-header">
          <h2 id={titleId} className="modal-title">
            {title}
          </h2>
          {headerActions ? <div className="modal-header-actions">{headerActions}</div> : null}
          <button
            type="button"
            className="modal-close"
            onClick={onClose}
            disabled={busy}
            aria-label="关闭对话框"
          >
            <X size={16} strokeWidth={1.8} aria-hidden="true" />
          </button>
        </header>
        <div className="modal-body">{children}</div>
        {actions ? <footer className="modal-actions">{actions}</footer> : null}
      </div>
    </div>,
    document.body,
  );
}
