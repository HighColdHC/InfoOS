import { useCallback, useEffect, useRef, useState, type DependencyList } from "react";
import { isAbort } from "../../../lib/isAbort";

type Fetcher<T> = ((signal: AbortSignal) => Promise<T>) | null;
type Options = { resetOnLoad?: boolean };

// deps describes the resource identity. A null fetcher disables and clears it.
// setData supports local updates such as appending a detail page after a mutation.
export function useResource<T>(fetcher: Fetcher<T>, deps: DependencyList, options: Options = {}) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(fetcher !== null);
  const [error, setError] = useState<string | null>(null);
  const latest = useRef({ fetcher, options });
  latest.current = { fetcher, options };
  const request = useRef<AbortController | null>(null);

  const reload = useCallback(async () => {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    const current = () => request.current === controller && !controller.signal.aborted;
    const { fetcher: load, options: settings } = latest.current;
    setError(null);
    if (!load) {
      setData(null);
      setLoading(false);
      return null;
    }
    if (settings.resetOnLoad) setData(null);
    setLoading(true);
    try {
      const result = await load(controller.signal);
      if (current()) setData(result);
      return current() ? result : null;
    } catch (reason) {
      if (current() && !isAbort(reason)) setError(reason instanceof Error ? reason.message : String(reason));
      return null;
    } finally {
      if (current()) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
    return () => request.current?.abort();
    // The caller supplies every value that changes the resource identity.
  }, deps);

  return { data, loading, error, reload, setData };
}
