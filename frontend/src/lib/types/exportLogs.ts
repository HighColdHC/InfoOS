export type ExportKind = "data" | "backup" | "diagnostic";
export type ExportFormat = "json" | "zip";
export type ExportRequest = {
  kind: ExportKind;
  format: ExportFormat;
  time_from: string | null;
  time_to: string | null;
  include_sources_runs: boolean;
  include_cards_content: boolean;
  include_asset_index: boolean;
  include_attachment_files: boolean;
  include_redacted_logs: boolean;
  include_media_files: boolean;
  log_levels: string[];
  include_environment_snapshot: boolean;
  source_run_ids: string[];
};

export type ExportEstimate = {
  normalized_request: ExportRequest;
  estimated_bytes: number;
  file_count: number;
  media_bytes: number;
};

export type ExportSubmitResult = {
  task_id: string;
  export_id: string;
  status: string;
  progress_url: string;
};

export type ExportArtifact = {
  file_name: string;
  size_bytes: number;
  sha256: string;
  logical_location: string;
  created_at: string;
  included_sections: string[];
  download_url: string;
};

export type ExportJobDetail = {
  task_id: string;
  export_id: string;
  kind: ExportKind;
  status: string;
  progress_percent: number;
  error: string | null;
  request: ExportRequest;
  artifact: ExportArtifact | null;
};

export type ExportJobPage = { items: ExportJobDetail[]; total: number; limit: number; offset: number; next_offset: number | null };

export type OperationLog = {
  log_id: string;
  timestamp: string;
  level: string;
  area: string;
  message: string;
  object_type: string | null;
  object_id: string | null;
  request_id: string | null;
  source_id: string | null;
  task_id: string | null;
  worker_id: string | null;
};

export type OperationsLogPage = {
  summary: Record<string, number>;
  items: OperationLog[];
  total: number;
  limit: number;
  offset: number;
  next_offset: number | null;
};

export type OperationsLogQuery = {
  query?: string;
  level?: string;
  area?: string;
  timeFrom?: string;
  timeTo?: string;
  objectId?: string;
  requestId?: string;
  limit?: number;
  offset?: number;
};
