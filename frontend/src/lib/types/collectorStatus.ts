export type CollectorStatus = {
  service_status: "ready" | "degraded" | "paused" | "unknown";
  latest_collection_at: string | null;
  reasons: string[];
  collection_status?: "active" | "paused";
  enabled_sources?: number;
};
