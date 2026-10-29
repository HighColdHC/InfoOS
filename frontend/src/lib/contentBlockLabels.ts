import type { KnownContentBlockKind } from "./types/common";

type ContentBlockCategory = "readable" | "detail";
// primary marks the main body kinds; the reader shows them without a heading.
type ContentBlockPresentation = { label: string; category: ContentBlockCategory; primary?: boolean };

// page_change and signal_matches are historical kinds still shown by the reader.
const contentBlockLabels: Record<KnownContentBlockKind | "page_change" | "signal_matches", ContentBlockPresentation> = {
  analysis: { label: "历史解读", category: "readable" },
  author_profile: { label: "作者简介", category: "readable" },
  card_content: { label: "正文", category: "readable", primary: true },
  enrichment: { label: "内容处理记录", category: "detail" },
  enrichment_gap: { label: "内容处理缺口", category: "detail" },
  external_doc: { label: "引用文档", category: "readable" },
  image: { label: "图片", category: "detail" },
  image_description: { label: "图片说明", category: "readable" },
  image_ocr: { label: "图片文字", category: "readable" },
  intel_summary: { label: "摘要", category: "readable" },
  linked_article: { label: "关联文章", category: "readable" },
  llm_context: { label: "处理上下文", category: "detail" },
  media_download_gap: { label: "附件处理缺口", category: "detail" },
  metadata: { label: "来源元数据", category: "detail" },
  opportunity_breakdown: { label: "商机拆解", category: "readable" },
  opportunity_verification: { label: "核实记录", category: "readable" },
  original_tweet: { label: "原始推文", category: "readable", primary: true },
  page_change: { label: "页面变化", category: "detail" },
  platform_comments: { label: "评论", category: "readable" },
  quote: { label: "引用内容", category: "readable" },
  quoted_tweet: { label: "引用内容", category: "readable" },
  reddit_metrics: { label: "互动数据", category: "detail" },
  reddit_outbound_link: { label: "外部链接记录", category: "detail" },
  reddit_post: { label: "Reddit 帖子", category: "readable", primary: true },
  relation_note: { label: "与你的关系", category: "readable" },
  reply_parent: { label: "上文", category: "readable" },
  repost_original: { label: "转发原文", category: "readable" },
  screening_evidence: { label: "初筛证据", category: "readable" },
  signal_matches: { label: "信号命中", category: "detail" },
  summary: { label: "历史摘要", category: "readable" },
  thread: { label: "对话上下文", category: "readable" },
  transcript: { label: "转录", category: "readable" },
  translation: { label: "中文翻译", category: "readable" },
  tweet: { label: "推文", category: "readable", primary: true },
  video: { label: "视频", category: "detail" },
  video_transcript: { label: "视频转录", category: "readable" },
  x_article: { label: "X 文章", category: "readable", primary: true },
};

export function contentBlockPresentation(kind: string): ContentBlockPresentation {
  if (Object.hasOwn(contentBlockLabels, kind)) {
    return contentBlockLabels[kind as keyof typeof contentBlockLabels];
  }
  return { label: `未知内容：${kind.trim().replace(/[_-]+/g, " ") || "未命名"}`, category: "detail" };
}
