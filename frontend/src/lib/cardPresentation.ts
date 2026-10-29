export function safeExternalUrl(value: string | null): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

export function completenessLabel(value: string): string {
  return value === "complete" ? "完整" : value === "partial" ? "部分缺失" : value === "processing" ? "处理中" : value;
}
