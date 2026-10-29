import type { AcquisitionEvidence } from "../acquisition";

export type SourceRunStatus = "completed" | "attention" | "running" | "pending" | string;
export type SourceRunExecutionScope = "all" | "executed" | "upcoming";
export type SourceRunTaskView = "all" | "collection" | "research";
export type SourceRunTaskKind = Exclude<SourceRunTaskView, "all">;
export type SourceRunExecutionBucket = "executed" | "running" | "due" | "future" | (string & {});
export type EvidenceAvailability = "available" | "partial" | "unavailable";

export type SourceRunCounts = {
  total: number | null;
  complete: number | null;
  partial: number | null;
  failed: number | null;
};

export type FetchStats = {
  total_links: number | null;
  dedup_skipped: number | null;
  pending_items: number | null;
  raw_item_count: number | null;
};

export type SourceRunSummaryMetrics = {
  last_24h: number;
  completed: number;
  attention: number;
  active: number;
};

export const taskQueueNames = ["public_fetch", "browser_fetch", "urgent_media", "card_pipeline", "gpu_asr", "cloud_ai", "maintenance_io"] as const;
export type TaskQueueName = (typeof taskQueueNames)[number] | (string & {});

export type TaskQueueSummary = {
  pending: number;
  running: number;
  failed: number;
  blocked: number;
  overdue: number;
  oldest_wait_seconds: number | null;
  running_worker_ids: string[];
  running_worker_count: number;
};

export type QueueTrackedTask = AcquisitionEvidence & {
  task_id: string;
  task_type: string;
  status: string;
  queue_name: TaskQueueName;
  priority: number;
  deadline_at: string | null;
  wait_seconds: number | null;
  overdue: boolean;
  claimed_by: string | null;
};

export type TaskTrackingPage = {
  items: QueueTrackedTask[];
  total: number;
  limit: number;
  offset: number;
  next_offset: number | null;
};

export type SourceRunListItem = AcquisitionEvidence & {
  source_run_id: string;
  root_task_id?: string;
  source_id: string;
  source_name: string;
  source_type?: string;
  task_view?: SourceRunTaskKind;
  trace_available?: boolean;
  platform: string;
  status: SourceRunStatus;
  execution_bucket?: SourceRunExecutionBucket;
  trigger: string;
  fetch_result: FetchStats;
  card_progress: SourceRunCounts;
  scheduled_at?: string | null;
  started_at: string | null;
  finished_at?: string | null;
  duration_ms: number | null;
  execution_status?: string;
  blocking_reason?: string | null;
  attempt_count?: number;
  pipeline?: { buckets?: { bucket: string; counts: { total: number; [status: string]: number } }[] };
  issues?: { code: string; count: number }[];
  queue_name?: TaskQueueName | null;
  priority?: number | null;
  deadline_at?: string | null;
  wait_seconds?: number | null;
  overdue?: boolean;
  claimed_by?: string | null;
};

export type BlockedSource = {
  source_id: string;
  source_name: string;
  platform: string;
  reason: string;
};

export type SourceRunListPage = {
  summary: SourceRunSummaryMetrics;
  /** False means total is a lower bound from the candidates classified so far. */
  total_exact: boolean;
  /** False means summary describes only the candidates classified so far. */
  counts_exact: boolean;
  queue_summary: Record<string, TaskQueueSummary>;
  items: SourceRunListItem[];
  total: number;
  next_offset: number | null;
};

export type SourceRunQuery = {
  query?: string;
  platform?: string;
  status?: string;
  executionScope?: SourceRunExecutionScope;
  taskView?: SourceRunTaskView;
  timeFrom?: string;
  timeTo?: string;
  limit?: number;
  offset?: number;
};

export type RetryableTaskEvidence = {
  task_id: string;
  status: string;
  error_summary?: string | null;
  retryable: boolean;
};

export type MediaBranch = {
  total: number | null;
  success: number | null;
  failed: number | null;
  retry_count: number | null;
  availability?: EvidenceAvailability;
  tasks: RetryableTaskEvidence[];
};

export type ContentEnrichEvidence = {
  availability: "available" | "unavailable";
  task_id: string;
  status: string;
  attempt: number | null;
  max_attempts: number | null;
  retryable: boolean;
  error: string | null;
};

export type TraceStage = {
  task_id: string;
  stage: string;
  status: string;
  attempt: number | null;
  max_attempts: number | null;
  retry_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  error?: { class: string; summary: string; next_action?: string } | null;
};

export type SourceRunTraceItem = {
  stages?: TraceStage[];
  media_branches?: { block_kind: string; status: string }[];
  card?: { id: string; title?: string };
  raw_item_id: string;
  card_id?: string | null;
  /** Persisted association is optional because older runs may lack provenance. */
  association?: string | null;
  card_projection_provenance?: string | null;
  title: string;
  content_enrich: ContentEnrichEvidence;
  image_download: MediaBranch;
  video_download: MediaBranch;
  video_transcription: MediaBranch;
  finalize: {
    status: string;
    version: number | string | null;
    completeness: string;
    task_id: string;
  };
};

export type SourceRunItemPage = {
  items: SourceRunTraceItem[];
  item_total: number;
  truncated: boolean;
  next_cursor: string | null;
};

export type SourceRunTrace = {
  events_archived: boolean;
  events_hot_window_days: number;
  run: AcquisitionEvidence & {
    source_run_id: string;
    root_task_id: string;
    source_id: string;
    source_name: string;
    source_type?: string;
    task_view?: SourceRunTaskKind;
    platform: string;
    trigger: string;
    status: SourceRunStatus;
    started_at: string | null;
    finished_at?: string | null;
    duration_ms: number | null;
    attempt_count: number;
    execution_status?: string;
    error?: TraceStage["error"];
    max_attempts?: number | null;
    retry_at?: string | null;
    fetch_stats: FetchStats;
    card_progress: SourceRunCounts;
  };
  discovery: {
    total_links: number | null;
    dedup_skipped: number | null;
    pending_items: number | null;
    availability: EvidenceAvailability;
    evidence: {
      executor: string | null;
      session_alias: string | null;
      sanitized_arguments: string[] | string | null;
      fetch_task_id: string | null;
      links: string[];
      availability: EvidenceAvailability;
    };
  };
  card_build: SourceRunCounts & { retry_count: number | null };
  retired_steps: { count: number; availability: EvidenceAvailability };
  items: SourceRunTraceItem[];
  item_page: SourceRunItemPage;
  task_tracking: QueueTrackedTask[];
  gaps: string[];
};

export type RetryTaskResult = { task_id: string; attempt: number; status: string };
