// ThemeProvider — light/dark mode with localStorage override and
// prefers-color-scheme fallback.
//
// The corresponding inline script in index.html already sets the
// data-theme attribute on <html> before first paint to avoid a flash
// of the wrong theme. This provider mirrors that initial value at
// mount, subscribes to system-preference changes, and exposes a
// useTheme() hook with a toggle helper.
//
// Theme storage key: "infoos.theme" (matches the inline script).

import { createContext, useCallback, useEffect, useMemo, useState } from "react";
import type { ReactElement, ReactNode } from "react";

export type Theme = "light" | "dark";

const STORAGE_KEY = "infoos.theme";

export type ThemeContextValue = {
  theme: Theme;
  setTheme: (next: Theme) => void;
  toggle: () => void;
};

export const ThemeContext = createContext<ThemeContextValue | null>(null);

function readInitialTheme(): Theme {
  // The inline boot script in index.html has already set data-theme.
  // Mirror its decision so the React state matches the DOM attribute.
  const attr = document.documentElement.getAttribute("data-theme");
  if (attr === "dark" || attr === "light") {
    return attr;
  }
  return "light";
}

function applyTheme(theme: Theme): void {
  document.documentElement.setAttribute("data-theme", theme);
}

export function ThemeProvider({ children }: { children: ReactNode }): ReactElement {
  const [theme, setThemeState] = useState<Theme>(readInitialTheme);

  // Keep the DOM attribute in sync (covers the case where the inline
  // script was skipped — e.g. storage disabled).
  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  // Track system-preference changes and follow them ONLY when the user
  // has not explicitly set a theme via the toggle. If localStorage has
  // a value, the user's explicit choice wins.
  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const handler = (event: MediaQueryListEvent) => {
      try {
        if (window.localStorage.getItem(STORAGE_KEY)) {
          return; // user has an explicit choice — don't override
        }
      } catch {
        return; // storage disabled — don't override either
      }
      const next: Theme = event.matches ? "dark" : "light";
      setThemeState(next);
    };
    media.addEventListener("change", handler);
    return () => media.removeEventListener("change", handler);
  }, []);

  const setTheme = useCallback((next: Theme) => {
    setThemeState(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Storage quota or privacy mode — theme still works in-memory
      // for this session, just won't persist.
    }
  }, []);

  const toggle = useCallback(() => {
    setThemeState((current) => {
      const next: Theme = current === "dark" ? "light" : "dark";
      try {
        window.localStorage.setItem(STORAGE_KEY, next);
      } catch {
        // Same fallback as setTheme.
      }
      return next;
    });
  }, []);

  const value = useMemo<ThemeContextValue>(
    () => ({ theme, setTheme, toggle }),
    [theme, setTheme, toggle],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}
