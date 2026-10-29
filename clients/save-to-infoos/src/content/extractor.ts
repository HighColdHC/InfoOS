import { Readability } from "@mozilla/readability";
import type { RenderedContent } from "../shared/types";

const FORBIDDEN_SELECTORS = [
  "script",
  "style",
  "noscript",
  "template",
  "iframe",
  "object",
  "embed",
  "form",
  "input",
  "textarea",
  "select",
  "option",
  "button",
  "[hidden]",
  '[aria-hidden="true"]'
].join(",");

const ALLOWED_TAGS = new Set([
  "A",
  "ARTICLE",
  "ASIDE",
  "B",
  "BLOCKQUOTE",
  "BR",
  "CAPTION",
  "CODE",
  "DD",
  "DEL",
  "DETAILS",
  "DIV",
  "DL",
  "DT",
  "EM",
  "FIGCAPTION",
  "FIGURE",
  "H1",
  "H2",
  "H3",
  "H4",
  "H5",
  "H6",
  "HR",
  "I",
  "IMG",
  "LI",
  "MAIN",
  "MARK",
  "OL",
  "P",
  "PRE",
  "S",
  "SECTION",
  "SMALL",
  "SPAN",
  "STRONG",
  "SUB",
  "SUMMARY",
  "SUP",
  "TABLE",
  "TBODY",
  "TD",
  "TFOOT",
  "TH",
  "THEAD",
  "TIME",
  "TR",
  "U",
  "UL"
]);

const GLOBAL_ALLOWED_ATTRIBUTES = new Set(["title"]);
const TAG_ATTRIBUTES: Record<string, Set<string>> = {
  A: new Set(["href"]),
  BLOCKQUOTE: new Set(["cite"]),
  IMG: new Set(["alt", "height", "src", "srcset", "title", "width"]),
  TD: new Set(["colspan", "rowspan"]),
  TH: new Set(["colspan", "rowspan", "scope"]),
  TIME: new Set(["datetime"])
};

function normalizedText(value: string): string {
  return value.replace(/\u00a0/g, " ").replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
}

function safeAbsoluteUrl(value: string, baseUrl: string): string | null {
  try {
    const resolved = new URL(value, baseUrl);
    return resolved.protocol === "http:" || resolved.protocol === "https:"
      ? resolved.toString()
      : null;
  } catch {
    return null;
  }
}

function safeSrcset(value: string, baseUrl: string): string | null {
  const candidates = value
    .split(",")
    .map((candidate) => candidate.trim())
    .filter(Boolean)
    .map((candidate) => {
      const [url, descriptor] = candidate.split(/\s+/, 2);
      const safeUrl = safeAbsoluteUrl(url, baseUrl);
      return safeUrl ? `${safeUrl}${descriptor ? ` ${descriptor}` : ""}` : null;
    })
    .filter((candidate): candidate is string => Boolean(candidate));
  return candidates.length ? candidates.join(", ") : null;
}

function sanitizeElement(element: Element, baseUrl: string): void {
  if (!ALLOWED_TAGS.has(element.tagName)) {
    element.replaceWith(...Array.from(element.childNodes));
    return;
  }

  const tagAllowed = TAG_ATTRIBUTES[element.tagName] ?? new Set<string>();
  for (const attribute of Array.from(element.attributes)) {
    const name = attribute.name.toLowerCase();
    if (
      name.startsWith("on") ||
      (!GLOBAL_ALLOWED_ATTRIBUTES.has(name) && !tagAllowed.has(name))
    ) {
      element.removeAttribute(attribute.name);
      continue;
    }

    if (name === "href" || name === "src" || name === "cite") {
      const safeUrl = safeAbsoluteUrl(attribute.value, baseUrl);
      if (safeUrl) {
        element.setAttribute(name, safeUrl);
      } else {
        element.removeAttribute(name);
      }
    } else if (name === "srcset") {
      const safeValue = safeSrcset(attribute.value, baseUrl);
      if (safeValue) {
        element.setAttribute(name, safeValue);
      } else {
        element.removeAttribute(name);
      }
    }
  }
}

export function sanitizeReadableHtml(
  html: string,
  baseUrl: string,
  domParser: DOMParser
): string {
  const parsed = domParser.parseFromString(html, "text/html");
  parsed.querySelectorAll(FORBIDDEN_SELECTORS).forEach((element) => element.remove());
  for (const element of Array.from(parsed.body.querySelectorAll("*")).reverse()) {
    sanitizeElement(element, baseUrl);
  }
  return `<article>${parsed.body.innerHTML.trim()}</article>`;
}

export function extractReadablePage(
  document: Document,
  baseUrl: string
): RenderedContent {
  const clone = document.cloneNode(true) as Document;
  clone.querySelectorAll(FORBIDDEN_SELECTORS).forEach((element) => element.remove());
  const article = new Readability(clone, {
    charThreshold: 20,
    keepClasses: false
  }).parse();

  if (!article?.content || !article.textContent?.trim()) {
    throw new Error("readable_content_unavailable");
  }

  const domParser = new DOMParser();
  const html = sanitizeReadableHtml(article.content, baseUrl, domParser);
  const parsedSanitized = domParser.parseFromString(html, "text/html");
  const text = normalizedText(parsedSanitized.body.textContent ?? article.textContent);
  if (!text) {
    throw new Error("readable_content_unavailable");
  }

  return {
    format: "readability_html",
    html,
    text,
    ...(article.byline ? { byline: normalizedText(article.byline) } : {}),
    ...(article.excerpt ? { excerpt: normalizedText(article.excerpt) } : {}),
    ...(article.siteName ? { site_name: normalizedText(article.siteName) } : {})
  };
}
