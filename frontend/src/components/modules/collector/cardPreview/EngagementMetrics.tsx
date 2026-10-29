import { Bookmark, CircleDollarSign, Eye, Heart, MessageCircle, Quote, Reply, Repeat2, Share2, ThumbsUp, Trophy } from "lucide-react";
import type { EngagementMetrics as EngagementMetricsType } from "../../../../lib/types/cardPreview";

type Props = { metrics: EngagementMetricsType };

const METRICS = [
  { key: "view_count", label: "浏览", icon: Eye },
  { key: "like_count", label: "点赞", icon: Heart },
  { key: "comment_count", label: "评论", icon: MessageCircle },
  { key: "reply_count", label: "回复", icon: Reply },
  { key: "repost_count", label: "转发", icon: Repeat2 },
  { key: "share_count", label: "分享", icon: Share2 },
  { key: "save_count", label: "收藏", icon: Bookmark },
  { key: "quote_count", label: "引用", icon: Quote },
  { key: "upvote_count", label: "赞同", icon: ThumbsUp },
  { key: "score", label: "分数", icon: Trophy },
  { key: "coin_count", label: "投币", icon: CircleDollarSign },
] as const;

export function EngagementMetrics({ metrics }: Props) {
  const availableMetrics = METRICS.filter(({ key }) => metrics[key] !== undefined);
  if (!availableMetrics.length) return null;

  return (
    <section className="collector-engagement-metrics" aria-label="互动指标">
      {availableMetrics.map(({ key, label, icon: Icon }) => (
        <div className="collector-engagement-metrics__item" key={key}>
          <Icon size={14} aria-hidden="true" />
          <span>{label}</span>
          <strong>{formatCount(metrics[key]!)}</strong>
        </div>
      ))}
    </section>
  );
}

function formatCount(value: number): string {
  return new Intl.NumberFormat("zh-CN").format(value);
}
