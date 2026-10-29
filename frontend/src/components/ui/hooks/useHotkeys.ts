// useHotkeys — bind keyboard shortcuts to a keymap.
//
// Supports both single-key bindings ("?", "/", "Escape") and two-key
// sequences ("g" then "b" for "go to brief"). When a sequence is
// matched, the second key must arrive within ``sequenceTimeoutMs`` of
// the first, otherwise the prefix is treated as a standalone key.
//
// Returns nothing; side effects install a document-level keydown
// listener that fires the matching handler. Handlers receive the
// KeyboardEvent so they can call event.preventDefault() if needed.

import { useEffect, useRef } from "react";

export type KeyHandler = (event: KeyboardEvent) => void;

export type Keymap = Partial<Record<string, KeyHandler>>;

// Keys that should fire even when the focus is in an editable element
// (textarea, input, contenteditable). Default behavior: skip handlers
// when the user is typing, since the keystroke almost certainly belongs
// to the input.
const DEFAULT_ALLOW_IN_EDITABLE = new Set<string>(["Escape"]);

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false;
  }
  const tag = target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") {
    return true;
  }
  if (target.isContentEditable) {
    return true;
  }
  return false;
}

function normalizeKey(event: KeyboardEvent): string {
  // Build a token like "g" or "?" or "Shift+?" or "/". We use ``key``
  // directly (already accounts for Shift on most keys) so the user
  // doesn't have to think about modifier names.
  if (event.key === " " || event.key === "Spacebar") {
    return "Space";
  }
  return event.key;
}

export type UseHotkeysOptions = {
  /** Two-key sequence timeout in ms. Default 800. */
  sequenceTimeoutMs?: number;
  /** When true, install the listener. Default true. */
  enabled?: boolean;
  /** Skip shortcuts for transient UI such as an open dialog. */
  shouldHandle?: (event: KeyboardEvent) => boolean;
};

export function useHotkeys(
  keymap: Keymap,
  options: UseHotkeysOptions = {},
): void {
  const { sequenceTimeoutMs = 800, enabled = true, shouldHandle } = options;
  // Hold the pending prefix (the first key of a two-key sequence) in
  // a ref so successive renders don't reset the timeout state.
  const pendingPrefix = useRef<string | null>(null);
  const pendingTimer = useRef<number | null>(null);

  useEffect(() => {
    if (!enabled) {
      return;
    }

    function clearPrefix() {
      pendingPrefix.current = null;
      if (pendingTimer.current !== null) {
        window.clearTimeout(pendingTimer.current);
        pendingTimer.current = null;
      }
    }

    function schedulePrefixExpiry() {
      if (pendingTimer.current !== null) {
        window.clearTimeout(pendingTimer.current);
      }
      pendingTimer.current = window.setTimeout(() => {
        pendingPrefix.current = null;
        pendingTimer.current = null;
      }, sequenceTimeoutMs);
    }

    function onKeyDown(event: KeyboardEvent) {
      if (shouldHandle && !shouldHandle(event)) { clearPrefix(); return; }
      // Don't capture modified keys like Ctrl+R (browser reload) or
      // Cmd+L (location bar focus) — they almost always belong to
      // the browser or OS, not the app.
      if (event.ctrlKey || event.metaKey || event.altKey) {
        return;
      }
      // If the focus is in an editable element, only fire handlers
      // explicitly opted in (e.g. Escape to close a modal).
      if (isEditableTarget(event.target) && !DEFAULT_ALLOW_IN_EDITABLE.has(event.key)) {
        clearPrefix();
        return;
      }
      const key = normalizeKey(event);
      const prefix = pendingPrefix.current;
      // If we have a pending prefix, try to complete a two-key sequence.
      if (prefix) {
        const sequence = `${prefix} ${key}`;
        const handler = keymap[sequence];
        clearPrefix();
        if (handler) {
          event.preventDefault();
          handler(event);
        }
        return;
      }
      // No prefix. Does this key start a two-key sequence?
      const hasSequence = Object.keys(keymap).some((entry) => entry.startsWith(`${key} `));
      if (hasSequence) {
        pendingPrefix.current = key;
        schedulePrefixExpiry();
        return;
      }
      // Single-key binding.
      const handler = keymap[key];
      if (handler) {
        event.preventDefault();
        handler(event);
      }
    }

    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      clearPrefix();
    };
  }, [keymap, sequenceTimeoutMs, enabled, shouldHandle]);
}
