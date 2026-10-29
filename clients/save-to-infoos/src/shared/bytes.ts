const encoder = new TextEncoder();

export function utf8ByteLength(value: string): number {
  return encoder.encode(value).byteLength;
}

export function truncateUtf8(value: string, maxBytes: number): string {
  if (maxBytes <= 0 || value.length === 0) {
    return "";
  }
  if (utf8ByteLength(value) <= maxBytes) {
    return value;
  }

  let low = 0;
  let high = value.length;
  while (low < high) {
    const middle = Math.ceil((low + high) / 2);
    const candidate = value.slice(0, middle);
    if (utf8ByteLength(candidate) <= maxBytes) {
      low = middle;
    } else {
      high = middle - 1;
    }
  }

  let result = value.slice(0, low);
  const finalCodeUnit = result.charCodeAt(result.length - 1);
  if (finalCodeUnit >= 0xd800 && finalCodeUnit <= 0xdbff) {
    result = result.slice(0, -1);
  }
  return result;
}
