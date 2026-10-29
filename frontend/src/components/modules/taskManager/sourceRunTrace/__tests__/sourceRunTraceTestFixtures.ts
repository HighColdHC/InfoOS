import type { SourceRunTrace } from "../../../../../lib/types/sourceRuns";

const traceItems: SourceRunTrace["items"] = [
  { raw_item_id: "raw-1", card_id: "card-1", title: "笔记 1", content_enrich: { availability: "available", task_id: "task-enrich-1", status: "succeeded", attempt: 1, max_attempts: 3, retryable: false, error: null }, image_download: { total: 7, success: 7, failed: 0, retry_count: 0, tasks: [] }, video_download: { total: 2, success: 1, failed: 1, retry_count: 2, tasks: [{ task_id: "task-video-1", status: "failed", error_summary: "下载超时", retryable: true }] }, video_transcription: { total: 2, success: 2, failed: 0, retry_count: 1, tasks: [] }, finalize: { status: "partial", version: 3, completeness: "partial", task_id: "task-finalize-1" } },
  { raw_item_id: "raw-2", card_id: "card-2", title: "笔记 2", content_enrich: { availability: "unavailable", task_id: "", status: "unavailable", attempt: null, max_attempts: null, retryable: false, error: null }, image_download: { total: 3, success: 3, failed: 0, retry_count: 0, tasks: [] }, video_download: { total: 0, success: 0, failed: 0, retry_count: 0, tasks: [] }, video_transcription: { total: null, success: null, failed: null, retry_count: null, availability: "unavailable", tasks: [] }, finalize: { status: "complete", version: 1, completeness: "complete", task_id: "task-finalize-2" } },
  { raw_item_id: "raw-3", card_id: "card-3", title: "笔记 3", content_enrich: { availability: "available", task_id: "task-enrich-3", status: "succeeded", attempt: 1, max_attempts: 3, retryable: false, error: null }, image_download: { total: 1, success: 1, failed: 0, retry_count: 0, tasks: [] }, video_download: { total: 0, success: 0, failed: 0, retry_count: 0, tasks: [] }, video_transcription: { total: 0, success: 0, failed: 0, retry_count: 0, tasks: [] }, finalize: { status: "complete", version: 1, completeness: "complete", task_id: "task-finalize-3" } },
];

export const traceFixture: SourceRunTrace = {
  events_archived: false,
  events_hot_window_days: 30,
  run: { source_run_id: "run-xhs-001", root_task_id: "task-root-001", source_id: "source-xhs-001", source_name: "小红书 · 产品观察", platform: "xhs", trigger: "manual", status: "attention", started_at: "2026-07-16T08:00:00Z", duration_ms: 81200, attempt_count: 2, fetch_stats: { total_links: 10, dedup_skipped: 3, pending_items: 7, raw_item_count: 7 }, card_progress: { total: 7, complete: 6, partial: 1, failed: 0 } },
  discovery: { total_links: 10, dedup_skipped: 3, pending_items: 7, availability: "available", evidence: { executor: "opencli", session_alias: "infoos-xhs", sanitized_arguments: ["xhs", "notes", "[REDACTED]"], fetch_task_id: "task-fetch-001", links: ["https://www.xiaohongshu.com/explore/note-1"], availability: "available" } },
  retired_steps: { count: 0, availability: "available" },
  card_build: { total: 7, complete: 6, partial: 1, failed: 0, retry_count: 1 },
  task_tracking: [
    { task_id: "task-fetch-001", task_type: "FETCH", status: "running", queue_name: "browser_fetch", priority: 90, deadline_at: "2026-07-16T08:30:00Z", wait_seconds: 3661, overdue: true, claimed_by: "worker-browser-1" },
    { task_id: "task-card-001", task_type: "CARD_BUILD", status: "pending", queue_name: "card_pipeline", priority: 50, deadline_at: null, wait_seconds: 12, overdue: false, claimed_by: null },
  ],
  items: traceItems,
  item_page: { items: traceItems, item_total: 3, truncated: false, next_cursor: null },
  gaps: ["笔记 2 的视频转录证据不可用"],
};
