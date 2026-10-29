import { useCallback, useEffect, useRef } from "react";

const DEFAULT_IDLE_MS = 10 * 60 * 1000;
const DEFAULT_ABSOLUTE_MS = 30 * 60 * 1000;
const HEARTBEAT_MIN_INTERVAL_MS = 5000;

type Props = {
  url: string;
  compact?: boolean;
  onExpired?: () => void;
  idleMs?: number;
  absoluteMs?: number;
};

export function BrowserConsoleFrame({ url, compact = false, onExpired, idleMs = DEFAULT_IDLE_MS, absoluteMs = DEFAULT_ABSOLUTE_MS }: Props) {
  const frameRef = useRef<HTMLIFrameElement>(null);
  const timerRef = useRef<number | null>(null);
  const absoluteTimerRef = useRef<number | null>(null);
  const lastHeartbeatRef = useRef(0);

  const heartbeat = useCallback(() => {
    const now = Date.now();
    if (now - lastHeartbeatRef.current < HEARTBEAT_MIN_INTERVAL_MS) return;
    lastHeartbeatRef.current = now;
    void window.fetch("/browser-console/heartbeat", {
      credentials: "same-origin",
      headers: { "X-Requested-With": "InfoOS-Browser-Console" },
    }).then((response) => {
      if (response.status === 401 || response.status === 403) onExpired?.();
    }).catch(() => {
      // The server-side WebSocket guard remains authoritative. A transient
      // heartbeat network failure should not close an otherwise live console.
    });
  }, [onExpired]);

  const resetIdleTimer = useCallback(() => {
    if (!onExpired) return;
    heartbeat();
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(onExpired, idleMs);
  }, [heartbeat, idleMs, onExpired]);

  useEffect(() => {
    resetIdleTimer();
    if (onExpired) absoluteTimerRef.current = window.setTimeout(onExpired, absoluteMs);
    return () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
      if (absoluteTimerRef.current !== null) window.clearTimeout(absoluteTimerRef.current);
    };
  }, [absoluteMs, onExpired, resetIdleTimer]);

  const attachActivityListeners = () => {
    resetIdleTimer();
    try {
      const frameWindow = frameRef.current?.contentWindow;
      const frameDocument = frameRef.current?.contentDocument;
      for (const target of [frameWindow, frameDocument]) {
        target?.addEventListener("pointerdown", resetIdleTimer, { capture: true, passive: true });
        target?.addEventListener("keydown", resetIdleTimer, { capture: true });
      }
    } catch {
      // The production console is same-origin. A browser that blocks frame
      // inspection still receives the hard server-side grant expiry.
    }
  };

  return <div className={`collector-browser-console${compact ? " is-compact" : ""}`}>
    <iframe
      ref={frameRef}
      src={url}
      title="InfoOS 容器浏览器"
      allow="clipboard-read; clipboard-write; fullscreen"
      onLoad={attachActivityListeners}
    />
  </div>;
}
