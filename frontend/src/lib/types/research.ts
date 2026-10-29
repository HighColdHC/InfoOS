export type ResearchSort = "general" | "latest" | "earliest" | "most-liked" | "most-commented" | "most-collected";

export type ResearchCapabilityStatus = "available" | "conditional" | "limited" | "unavailable" | string;

export type ResearchCapability = {
  status: ResearchCapabilityStatus;
  mode?: string | null;
  max_items_limit: number;
  sort_options: ResearchSort[];
  default_sort: ResearchSort;
  supports_date_range: boolean;
  requires_source_selection: boolean;
  limitation?: string | null;
};

export type ResearchPlatformCapability = {
  platform: string;
  label: string;
  research: ResearchCapability;
};

export type ResearchPlatformRequest = {
  platform: string;
  max_items: number;
  sort?: ResearchSort;
  options: { source_ids?: string[]; content_type?: "all" | "essence" | "bid" | "topic" | "opportunity" | "square" };
};

export type ResearchRunRequest = {
  name: string;
  keywords: string[];
  date_range?: { start_date: string; end_date: string; timezone: string };
  platforms: ResearchPlatformRequest[];
};

export type ResearchRunSummary = {
  research_run_id: string;
  name: string;
  status: string;
  keywords: string[];
  created_at?: string | null;
  updated_at?: string | null;
  platform_count?: number;
  completed_platform_count?: number;
  failed_platform_count?: number;
};

export type ResearchRunSubmitResult = { ok: boolean; replayed: boolean; research_run: ResearchRunSummary };
export type ResearchRunListResult = { ok: boolean; research_runs: ResearchRunSummary[] };

export type ResearchPlatformRun = {
  platform: string;
  label?: string;
  status: string;
  source_run_id?: string | null;
  error?: string | null;
  limitation?: string | null;
  accepted_items?: number | null;
};

export type ResearchCoverage = {
  platform: string;
  keyword?: string | null;
  stop_reason?: string | null;
  completeness?: string | null;
  limitation?: string | null;
  visible_count?: number | null;
  accepted_count?: number | null;
};

export type ResearchCardItem = {
  card_id: string;
  title: string;
  platform?: string | null;
  published_at?: string | null;
  source_url?: string | null;
};

export type ResearchRunDetail = {
  research_run: ResearchRunSummary;
  platform_runs: ResearchPlatformRun[];
  coverage: ResearchCoverage[];
  item_page: { items: ResearchCardItem[]; next_cursor: string | null };
};
