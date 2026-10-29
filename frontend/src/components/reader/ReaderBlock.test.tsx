import { render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { ContentBlock } from "../../lib/types/card";
// @ts-expect-error Tests execute in Node; production code omits Node typings.
import { readFileSync } from "node:fs";
import { ReaderBlock } from "./ReaderBlock";

declare const process: { cwd: () => string };
const readerCss = readFileSync(`${process.cwd()}/src/styles/_card-reader.css`, "utf8");

// ReaderBlock lazy-loads its Markdown renderer; wait until the fallback is gone.
async function renderBlock(content: string, block_kind = "card_content") {
  const view = render(<main className="standalone-card-reader">
    <ReaderBlock block={{ ...block, block_kind, content }} />
  </main>);
  await waitFor(() => expect(view.container.querySelector(".card-reader__markdown [role=status]")).toBeNull());
  return view;
}

const block: ContentBlock = {
  block_id: "wrap", card_id: "wrap", block_kind: "card_content", layer: "original",
  content: "", status: "preserved", source_url: null, provenance: null, sort_order: 0,
};

describe("reader text wrapping", () => {
  let style: HTMLStyleElement;
  beforeEach(() => {
    style = document.createElement("style");
    style.textContent = readerCss;
    document.head.append(style);
  });
  afterEach(() => style.remove());

  it.each(["card_content", "screening_evidence", "platform_comments", "author_profile", "video_transcript"])(
    "preserves single newlines and separate paragraphs in %s", async (block_kind) => {
      const { container } = await renderBlock("a\nb\n\nc", block_kind);
      const paragraphs = container.querySelectorAll(".card-reader__markdown p");
      expect([...paragraphs].map(p => p.textContent)).toEqual(["a\nb", "c"]);
      expect(getComputedStyle(paragraphs[0]).whiteSpace).toBe("pre-line");
      expect(getComputedStyle(container.querySelector(".card-reader__markdown")!).overflowWrap).toBe("anywhere");
    },
  );

  it("keeps Markdown lists, quotes, code, tables and links structured", async () => {
    const content = "- first\n  continuation\n- second\n\n> quoted\n> next\n\n```txt\n  a\n    b\n```\n\n| key | value |\n| --- | --- |\n| one | two |\n\n[link](https://example.test)";
    const { container } = await renderBlock(content);
    expect(container.querySelectorAll("li")).toHaveLength(2);
    expect(container.querySelector("li")?.textContent).toBe("first\ncontinuation");
    expect(getComputedStyle(container.querySelector("li")!).whiteSpace).toBe("pre-line");
    expect(container.querySelector("blockquote p")?.textContent).toBe("quoted\nnext");
    expect(container.querySelector("pre code")?.textContent).toBe("  a\n    b\n");
    expect(getComputedStyle(container.querySelector("pre")!).whiteSpace).toBe("pre-wrap");
    expect(container.querySelectorAll("td")).toHaveLength(2);
    expect(getComputedStyle(container.querySelector("table")!).tableLayout).toBe("fixed");
    expect(container.querySelector("a")).toHaveAttribute("href", "https://example.test");
  });

  it("does not double hard breaks or expose structural whitespace in loose lists", async () => {
    const { container } = await renderBlock("a  \nb\n\n- first\n\n  second\n- third");
    expect(container.querySelector("p")?.textContent).toBe("a\nb");
    expect(getComputedStyle(container.querySelector("br")!).display).toBe("none");
    expect(getComputedStyle(container.querySelector("li")!).whiteSpace).toBe("normal");
    expect(getComputedStyle(container.querySelector("li p")!).whiteSpace).toBe("pre-line");
  });
});
