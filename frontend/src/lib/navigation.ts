// Browser feature availability is a runtime boundary, independent of lib.dom declarations.
type ViewTransitionDocument = Omit<Document, "startViewTransition"> & { startViewTransition?: (update: () => void) => unknown };

/** Run a synchronous DOM update inside a view transition when the browser supports it. */
export function withViewTransition(update: () => void): void {
  const doc = document as ViewTransitionDocument;
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (!doc.startViewTransition || reduced) {
    update();
    return;
  }
  doc.startViewTransition(update);
}

export function navigateToMenu(menu: string, params: Record<string, string> = {}) {
  const url = new URL(window.location.href);
  url.search = "";
  url.searchParams.set("menu", menu);
  for (const [key, value] of Object.entries(params)) url.searchParams.set(key, value);
  withViewTransition(() => {
    window.history.pushState(window.history.state, "", `${url.pathname}${url.search}${url.hash}`);
    window.dispatchEvent(new Event("infoos:navigate"));
  });
}
