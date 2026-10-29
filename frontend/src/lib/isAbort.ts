export function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}
