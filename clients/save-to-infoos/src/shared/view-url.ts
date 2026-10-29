export function resolveInfoosViewUrl(
  baseUrl: string,
  infoosUrl: string
): string | undefined {
  try {
    const base = new URL(baseUrl);
    const resolved = new URL(infoosUrl, `${base.origin}/`);
    return resolved.origin === base.origin ? resolved.toString() : undefined;
  } catch {
    return undefined;
  }
}
