import { platformLabel } from "./platforms";

const REASONS: Record<string, string> = {
  douyin_browser_no_media_resources: "页面里没找到视频资源",
  douyin_browser_identity_mismatch: "打开的页面与卡片不一致",
  douyin_video_download_failed: "抖音视频下载失败",
  douyin_browser_opencli_unavailable_or_timeout: "浏览器采集超时",
  xiaohongshu_media_download_failed: "小红书媒体下载失败",
  x_media_download_fallback_failed: "X 媒体下载失败",
  x_media_download_browser_session: "X 登录状态需检查",
  x_media_download_no_media_found: "没找到可下载的媒体",
  x_media_unattributed: "媒体与帖子对不上",
  direct_media_download_failed: "直链下载失败",
  command_failed: "下载工具执行失败",
};

export function mediaGapLabel(reason: string): string {
  if (Object.hasOwn(REASONS, reason)) return REASONS[reason];
  if (reason.startsWith("youtube_")) return "YouTube 视频下载失败";
  const prefix = reason.split("_")[0];
  const label = platformLabel(prefix);
  return prefix && label !== prefix ? `${label}媒体下载失败` : "媒体下载未成功";
}
