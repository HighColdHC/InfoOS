import { describe, expect, it } from "vitest";
// @ts-expect-error Tests execute in Node; browser production code intentionally omits Node typings.
import { readFileSync } from "node:fs";
// @ts-expect-error Tests execute in Node; browser production code intentionally omits Node typings.
import { resolve } from "node:path";

declare const process: { cwd: () => string };
const stylesheetNames = ["_collector-cards.css", "_collector-tasks.css", "_collector-environment.css", "_collector-export-logs.css"];
const retiredAliases = ["accent", "border-default", "border-subtle", "danger", "success", "surface-base", "surface-raised", "surface-sunken", "text-primary", "text-secondary", "text-tertiary", "warning"];
const readStyle = (name: string) => readFileSync(resolve(process.cwd(), "src/styles", name), "utf8");
const names = (source: string, pattern: RegExp) => new Set(Array.from(source.matchAll(pattern), (match) => match[1]));

describe("collector stylesheet token contract", () => {
  it.each(stylesheetNames)("uses only tokens defined by tokens.css in %s", (stylesheetName) => {
    const tokens = readStyle("tokens.css");
    const stylesheet = readStyle(stylesheetName);
    expect(stylesheet).toContain(".collector-");
    const defined = new Set([...names(tokens, /(--[\w-]+)\s*:/g), ...names(stylesheet, /(--[\w-]+)\s*:/g)]);
    const referenced = names(stylesheet, /var\((--[\w-]+)/g);
    const missing = [...referenced].filter((token) => !defined.has(token));
    expect(missing, `${stylesheetName} references undefined design tokens`).toEqual([]);
    for (const alias of retiredAliases) expect(stylesheet).not.toContain(`var(--${alias})`);
  });

  it("wraps arbitrary media-preview title ids instead of hiding overflowing content", () => {
    const stylesheet = readStyle("_collector-cards.css");
    const titleRule = stylesheet.match(/\.collector-media-preview__title\s*\{([^}]*)\}/)?.[1] ?? "";
    expect(titleRule).toContain("overflow-wrap: anywhere");
    expect(titleRule).not.toMatch(/overflow(?:-x)?:\s*(?:hidden|clip)/);
  });
});
