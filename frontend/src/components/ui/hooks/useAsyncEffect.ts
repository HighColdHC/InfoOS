// useAsyncEffect — async effect with AbortController-based cleanup.
// Replaces the `let alive = true` pattern. The effect receives an
// AbortSignal; callers can pass it to fetch(), listen for the
// "abort" event, or simply throw. Errors thrown after an abort are
// silenced (a common case when a fetch races with unmount).

import { useEffect } from "react";

export type UseAsyncEffectOptions = {
  onError?: (err: unknown) => void;
};

export function useAsyncEffect(
  effect: (signal: AbortSignal) => Promise<void> | void,
  deps: React.DependencyList,
  options?: UseAsyncEffectOptions,
): void {
  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;

    (async () => {
      try {
        await effect(signal);
      } catch (err) {
        if (signal.aborted) return;
        if (options?.onError) {
          options.onError(err);
        } else {
          console.error("[useAsyncEffect]", err);
        }
      }
    })();

    return () => controller.abort();
  }, deps);
}
