import { describe, expect, it } from "vitest";
import { mediaGapLabel } from "./mediaGapLabels";

describe("mediaGapLabel", () => {
  it.each([
    ["douyin_browser_no_media_resources", "页面里没找到视频资源"],
    ["douyin_browser_identity_mismatch", "打开的页面与卡片不一致"],
    ["douyin_video_download_failed", "抖音视频下载失败"],
    ["douyin_browser_opencli_unavailable_or_timeout", "浏览器采集超时"],
    ["xiaohongshu_media_download_failed", "小红书媒体下载失败"],
    ["x_media_download_fallback_failed", "X 媒体下载失败"],
    ["x_media_download_browser_session", "X 登录状态需检查"],
    ["x_media_download_no_media_found", "没找到可下载的媒体"],
    ["x_media_unattributed", "媒体与帖子对不上"],
    ["direct_media_download_failed", "直链下载失败"],
    ["command_failed", "下载工具执行失败"],
    ["youtube_media_download_no_video_file", "YouTube 视频下载失败"],
    ["youtube_future_reason", "YouTube 视频下载失败"],
    ["douyin_future_reason", "抖音媒体下载失败"],
    ["xiaohongshu_future_reason", "小红书媒体下载失败"],
    ["x_future_reason", "X媒体下载失败"],
    ["reddit_future_reason", "Reddit媒体下载失败"],
    ["future_reason", "媒体下载未成功"],
    ["", "媒体下载未成功"],
  ])("translates %s", (reason, expected) => {
    expect(mediaGapLabel(reason)).toBe(expected);
  });
});
