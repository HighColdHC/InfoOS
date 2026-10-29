export function isImageUrl(url: string): boolean {
  return /\.(jpg|jpeg|png|gif|webp|svg|bmp)(\?.*)?$/i.test(url);
}

export function isVideoUrl(url: string): boolean {
  return /\.(mp4|webm|ogg|mov)(\?.*)?$/i.test(url);
}

export function resolveMediaUrl(url: string): string {
  const value = url.trim();
  if (!value) return value;
  if (value.startsWith("http://") || value.startsWith("https://") || value.startsWith("/api/assets/")) {
    return value;
  }
  const assetPath = localAssetPath(value);
  if (!assetPath && value.startsWith("/")) return value;
  return `/api/assets/${assetPath || value.replace(/^\/+/, "")}`;
}

function localAssetPath(value: string): string {
  const marker = "/data/assets/";
  if (value.includes(marker)) {
    return value.slice(value.indexOf(marker) + marker.length);
  }
  if (value.startsWith("data/assets/")) {
    return value.slice("data/assets/".length);
  }
  return "";
}
