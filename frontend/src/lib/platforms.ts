export type CollectorPlatformOption = { value: string; label: string };

const PLATFORM_LABELS: Record<string, string> = {
  x: "X",
  youtube: "YouTube",
  rss: "RSS",
  reddit: "Reddit",
  github: "GitHub",
  hackernews: "Hacker News",
  producthunt: "Product Hunt",
  xiaohongshu: "小红书",
  douyin: "抖音",
  zsxq: "生财有术",
  scys: "生财有术",
  web: "网页监控",
  web_monitor: "网页监控",
};

const DEFAULT_PLATFORMS = ["x", "youtube", "rss", "reddit", "github", "hackernews", "producthunt", "xiaohongshu", "douyin", "zsxq"];

export function platformLabel(platform: string | null | undefined): string {
  return platform ? PLATFORM_LABELS[platform] ?? platform : "卡片";
}

export function collectorPlatformOptions(observed: Array<string | null | undefined> = []): CollectorPlatformOption[] {
  const values = [...new Set([...DEFAULT_PLATFORMS, ...observed.filter((value): value is string => Boolean(value))])];
  return values.map((value) => ({ value, label: platformLabel(value) }));
}
