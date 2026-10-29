// ThemeProvider tests — toggle, persistence, and prefers-color-scheme
// fallback.

import { render, screen, fireEvent, act } from "@testing-library/react";
import { ThemeProvider, ThemeContext } from "../ThemeProvider";
import { useContext } from "react";
import { describe, test, expect, beforeEach } from "vitest";

function ThemeProbe() {
  const { theme, toggle, setTheme } = useContext(ThemeContext)!;
  return (
    <div>
      <span data-testid="theme">{theme}</span>
      <button type="button" onClick={toggle}>toggle</button>
      <button type="button" onClick={() => setTheme("light")}>light</button>
      <button type="button" onClick={() => setTheme("dark")}>dark</button>
    </div>
  );
}

describe("ThemeProvider", () => {
  beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
  });

  test("defaults to light when no preference is set", () => {
    render(
      <ThemeProvider>
        <ThemeProbe />
      </ThemeProvider>
    );
    expect(screen.getByTestId("theme").textContent).toBe("light");
  });

  test("toggle flips the theme and persists to localStorage", () => {
    render(
      <ThemeProvider>
        <ThemeProbe />
      </ThemeProvider>
    );
    expect(screen.getByTestId("theme").textContent).toBe("light");
    act(() => {
      fireEvent.click(screen.getByText("toggle"));
    });
    expect(screen.getByTestId("theme").textContent).toBe("dark");
    expect(window.localStorage.getItem("infoos.theme")).toBe("dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    act(() => {
      fireEvent.click(screen.getByText("toggle"));
    });
    expect(screen.getByTestId("theme").textContent).toBe("light");
    expect(window.localStorage.getItem("infoos.theme")).toBe("light");
  });

  test("setTheme writes to localStorage and updates the DOM attribute", () => {
    render(
      <ThemeProvider>
        <ThemeProbe />
      </ThemeProvider>
    );
    act(() => {
      fireEvent.click(screen.getByText("dark"));
    });
    expect(screen.getByTestId("theme").textContent).toBe("dark");
    expect(window.localStorage.getItem("infoos.theme")).toBe("dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });

  test("reads initial theme from data-theme attribute (set by inline boot script)", () => {
    // Simulate the inline script in index.html having set the attribute.
    document.documentElement.setAttribute("data-theme", "dark");
    render(
      <ThemeProvider>
        <ThemeProbe />
      </ThemeProvider>
    );
    expect(screen.getByTestId("theme").textContent).toBe("dark");
  });

});
