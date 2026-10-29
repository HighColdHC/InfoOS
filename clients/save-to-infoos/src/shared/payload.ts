import { truncateUtf8, utf8ByteLength } from "./bytes";
import type {
  CaptureCapabilities,
  CaptureMode,
  CaptureRequest,
  PageContext,
  RenderedContent
} from "./types";

export class PayloadValidationError extends Error {
  constructor(public readonly code: "invalid_url" | "url_too_large" | "content_too_large") {
    super(code);
  }
}

function safeHttpUrl(value: string | null): string | null {
  if (!value) {
    return null;
  }
  try {
    const parsed = new URL(value);
    return parsed.protocol === "http:" || parsed.protocol === "https:"
      ? parsed.toString()
      : null;
  } catch {
    return null;
  }
}

function optionalTrimmedText(value: string | null, maxBytes: number): string | null {
  const trimmed = value?.trim();
  return trimmed ? truncateUtf8(trimmed, maxBytes) : null;
}

export function createClientCaptureId(): string {
  return `cap_${crypto.randomUUID()}`;
}

export function buildCaptureRequest(input: {
  context: PageContext;
  mode: CaptureMode;
  renderedContent: RenderedContent | null;
  capabilities: CaptureCapabilities;
  clientCaptureId: string;
  clientVersion: string;
  capturedAt?: string;
}): CaptureRequest {
  const url = safeHttpUrl(input.context.url);
  if (!url) {
    throw new PayloadValidationError("invalid_url");
  }
  if (utf8ByteLength(url) > input.capabilities.limits.url_bytes) {
    throw new PayloadValidationError("url_too_large");
  }

  const canonicalUrl = safeHttpUrl(input.context.canonicalUrl);
  const boundedCanonical =
    canonicalUrl &&
    utf8ByteLength(canonicalUrl) <= input.capabilities.limits.url_bytes
      ? canonicalUrl
      : null;

  if (input.mode === "readable_content") {
    if (!input.renderedContent) {
      throw new PayloadValidationError("content_too_large");
    }
    if (
      !input.capabilities.capabilities.includes("readable_content") ||
      !input.capabilities.accepted_content_formats.includes("readability_html") ||
      utf8ByteLength(JSON.stringify(input.renderedContent)) >
        input.capabilities.limits.rendered_content_bytes
    ) {
      throw new PayloadValidationError("content_too_large");
    }
  }

  return {
    schema: "infoos.capture-request.v1",
    client_capture_id: input.clientCaptureId,
    url,
    canonical_url: boundedCanonical,
    title: truncateUtf8(
      input.context.title.trim() || url,
      input.capabilities.limits.title_bytes
    ),
    selected_text: optionalTrimmedText(
      input.context.selectedText,
      input.capabilities.limits.selected_text_bytes
    ),
    page_language: input.context.pageLanguage?.trim() || null,
    captured_at: input.capturedAt ?? new Date().toISOString(),
    capture_mode: input.mode,
    rendered_content: input.mode === "readable_content" ? input.renderedContent : null,
    client: {
      name: "save-to-infoos",
      version: input.clientVersion
    }
  };
}
