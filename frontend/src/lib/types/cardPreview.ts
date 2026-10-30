import type { ContentBlock } from "./card";

export type CardCompleteness = "complete" | "partial" | "processing" | (string & {});
export type CardPreviewEvaluationView = "passed" | "evaluated" | "unevaluated";
export type CardPreviewFacets = {
  platforms: { platform: string; label: string; card_count: number }[];
  sources: { source_id: string; name: string; platform: string; card_count: number; evaluated_count?: number; passed_count?: number }[];
  limits: { platforms: number | null; sources: number | null };
  truncated: { platforms: boolean; sources: boolean };
};

export type CardPreviewSort = "published_desc" | "updated_desc" | "relevance_desc";

export type CardPreviewEvaluation = {
  quality: number;
  relevance: number | null;
  passed_quality: boolean;
  attribution_kind: string | null;
  attribution_ref: string | null;
  content_type: string;
  suggested_action: string;
  summary: string;
  relation_note: string;
  evaluated_at: string;
  prompt_version: string;
  profile_version: number;
  model: string;
};

export type CardPreviewListItem = {
  card_id: string;
  title: string;
  author: string | null;
  platform: string | null;
  is_digest?: boolean | null;
  published_at: string | null;
  first_seen_at: string | null;
  completeness_status: CardCompleteness;
  attachment_kinds: string[];
  version: number;
  updated_at: string | null;
  source_label: string | null;
  source_url: string | null;
  excerpt?: string | null;
  summary_excerpt?: string | null;
  cover_kind?: "image" | "video" | null;
  cover_asset_url?: string | null;
  cover_thumbnail_url?: string | null;
  media_count?: number;
  evaluation: CardPreviewEvaluation | null;
  opportunity?: { has_breakdown: boolean; next_step: "verify" | "observe" | "skip" } | null;
};

export type CardPreviewPage = {
  items: CardPreviewListItem[];
  total: number;
  processing_total: number;
  limit: number;
  offset: number;
  sort?: CardPreviewSort;
};

export type CardPreviewAsset = {
  asset_id: string;
  card_id: string;
  asset_kind: "image" | "video" | (string & {});
  mime_type?: string | null;
  status?: string | null;
  source_url?: string | null;
  remote_url?: string | null;
  local_path?: string | null;
  size_bytes?: number | null;
  content_hash?: string | null;
  cleanup_state?: string | null;
  asset_url: string;
  thumbnail_url?: string | null;
};

export type EngagementMetrics = Partial<Record<
  "view_count" | "like_count" | "comment_count" | "reply_count" | "repost_count" | "share_count" | "save_count" | "quote_count" | "upvote_count" | "score" | "coin_count",
  number
>>;

export type CardPreviewCard = Omit<CardPreviewListItem, "attachment_kinds" | "source_label" | "source_url" | "evaluation"> & {
  captured_at: string | null;
  status: string;
  content_hash: string | null;
  processor_version: string | null;
  source_label: string | null;
  source_url: string | null;
  raw_item_id: string | null;
  source_run_id: string | null;
  attachment_count: number;
  engagement_metrics: EngagementMetrics;
};

export type CardPreviewTrace = {
  raw_connector_id?: string | null;
  raw_item_id?: string | null;
  source_run_id?: string | null;
  [key: string]: unknown;
};

export type ProviderRecoveryState =
  | "none"
  | "waiting_circuit"
  | "scheduled"
  | "running"
  | "exhausted"
  | "recovered"
  | (string & {});

export type ProviderRecovery = {
  state: ProviderRecoveryState;
  trigger: "automatic" | "manual" | null;
  provider_id: string | null;
  outage_epoch: number | null;
  automatic_recoveries_used: number;
  automatic_recovery_limit: number;
  next_retry_at: string | null;
  manual_retry_allowed: boolean;
};

export type CardPreviewDetail = {
  knowledge_cards: import("./knowledge").KnowledgeLink[];
  opportunity: { has_breakdown: boolean; next_step: "verify" | "observe" | "skip" } | null;
  card: CardPreviewCard;
  body_blocks: ContentBlock[];
  comment_blocks: ContentBlock[];
  image_assets: CardPreviewAsset[];
  video_assets: CardPreviewAsset[];
  media_gap: { reason: string; status: string } | null;
  missing_reasons: string[];
  trace: CardPreviewTrace;
  evaluation: CardPreviewEvaluation | null;
};

export type CardPreviewQuery = {
  published_since?: string;
  published_until?: string;
  source_id?: string;
  evaluation?: CardPreviewEvaluationView;
  query?: string;
  platform?: string;
  completeness?: string;
  status?: string;
  limit?: number;
  offset?: number;
  sort?: CardPreviewSort;
  recommended?: boolean;
  digest?: 0 | 1;
};

export type CapturePreviewResolution = {
  capture_id: string;
  processing_status: "pending" | "running" | "succeeded" | "failed";
  card_id: string | null;
  infoos_url: string;
  error_code: string | null;
  updated_at: string | null;
};
