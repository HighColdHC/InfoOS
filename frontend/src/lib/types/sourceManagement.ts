export type ManagedSourceHealthStatus = "healthy" | "needs_attention" | "degraded" | "unknown" | (string & {});

export type SourceManagementSummary = {
  total_sources: number;
  enabled_sources: number;
  needs_attention: number;
  reliability_7d: number;
};

export type SourceManagementItem = {
  source_id: string;
  display_name: string;
  platform: string;
  source_type: string;
  identifier: string;
  health_status: ManagedSourceHealthStatus;
  health_label: string;
  status_explanation: string | null;
  cadence_hours: number;
  max_items_per_run: number;
  auto_enrich_media: boolean;
  enabled: boolean;
  latest_run_at: string | null;
  latest_run_status: string | null;
  readiness_status: string | null;
  login_status: string | null;
  connector_id: string | null;
  notes?: string | null;
  next_run_at?: string | null;
  generated_card_count: number;
  current_config_version?: string | null;
  config_effective_at?: string | null;
  raw_metadata: SourceRawMetadata;
};

export type SourceCadenceSuggestion = {
  suggested_cadence_hours: number | null;
  new_items_7d: number;
  reason: string;
};

export type SourceAiProcessing = {
  enabled: boolean;
  provider_id?: string;
  model?: string;
  prompt?: string;
  target_language?: string;
};

export type SourceRawMetadata = Record<string, unknown> & {
  auto_enrich_media: boolean;
  ai_processing?: SourceAiProcessing;
  feed_provider: "generic";
  feed_content_mode: "feed_preferred";
  include_top_comments?: boolean;
  keyword_search?: {
    sort?: "general" | "latest" | "most-liked" | "most-commented" | "most-collected";
    publish_time?: "any" | "day" | "week" | "half-year";
  };
  zsxq_search?: {
    sort?: "comprehensive" | "newest" | "earliest" | "most-favorited" | "most-commented" | "most-liked" | "most-coins" | "recent-coins";
    content_type?: "all" | "essence" | "bid" | "topic" | "opportunity" | "square";
    keywords?: string[];
    start_date?: string;
    end_date?: string;
  };
};

export type SourceManagementPage = {
  summary: SourceManagementSummary;
  items: SourceManagementItem[];
  total: number;
  limit: number;
  offset: number;
  cadence_suggestions?: Record<string, SourceCadenceSuggestion>;
};

export type SourceRecentRunStats = import("../acquisition").AcquisitionEvidence & {
  total: number;
  success: number;
  failed: number;
  cards_created: number;
  last_run_at: string | null;
  last_status: string | null;
};

export type SourceManagementDetail = {
  source: SourceManagementItem;
  cadence_suggestion?: SourceCadenceSuggestion;
  status_explanation: string;
  current_config_version: string;
  config_effective_at: string;
  recent_run_stats: SourceRecentRunStats;
};

export type SourceConfigurationPayload = {
  platform: string;
  source_type: string;
  identifier: string;
  display_name: string;
  cadence_hours: number;
  max_items_per_run: number;
  enabled: boolean;
  notes?: string | null;
  raw_metadata: SourceRawMetadata;
};

export type SourceManagementResult = {
  source: SourceManagementItem;
  source_id: string;
  source_run_id: string | null;
  task_id: string | null;
  initial_fetch: boolean;
  reused: boolean;
};

export type SourceRunSubmissionResult = {
  source_run_id: string | null;
  task_id: string;
  batch_id: string;
};

export type SourcePlatformCapability = {
  platform: string;
  label: string;
  source_types: SourceTypeCapability[];
  cadence_options: number[];
  max_items_limit: number;
  default_max_items: number;
  configurable?: boolean;
};

export type SourceTypeCapability = {
  source_type: string;
  label: string;
  default_cadence_hours: number;
  default_max_items: number | null;
  identifier_mode: "text" | "select";
  identifier_options: Array<{ value: string; label: string }>;
  identifier_hint: string | null;
  requires_login: "required" | "optional" | "no" | string;
  login_verifiable: "verifiable" | "n/a" | string;
  max_items_limit: number;
};

export type SourcePageQuery = {
  query?: string;
  platform?: string;
  status?: string;
  limit?: number;
  offset?: number;
};
