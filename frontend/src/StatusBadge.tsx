import type { BlockStatus } from "./lib/api";

type StatusConfig = {
  label: string;
  icon: string;
  className: string;
};

const statusMap: Record<BlockStatus, StatusConfig | null> = {
  preserved: null,
  downloaded: null,
  enriched: null,
  body_fetched: null,
  done: null,
  pending: { label: "处理中", icon: "⏳", className: "status-badge status-pending" },
  blocked: { label: "已阻止", icon: "🚫", className: "status-badge status-blocked" },
  failed: { label: "失败", icon: "❌", className: "status-badge status-failed" },
  cleaned: { label: "Cleaned", icon: "🧹", className: "status-badge status-cleaned" },
  not_found: { label: "未找到", icon: "", className: "status-badge status-not-found" },
  unsupported: { label: "不适用", icon: "", className: "status-badge status-unsupported" },
  depth_limited: { label: "Depth limited", icon: "📏", className: "status-badge status-depth-limited" }
};

type StatusBadgeProps = {
  status: BlockStatus;
  onRetry?: () => void;
};

export default function StatusBadge({ status, onRetry }: StatusBadgeProps) {
  const config = statusMap[status];
  if (!config) return null;

  return (
    <span className={config.className} role="status" aria-label={`Status: ${config.label}`}>
      {config.icon && <span className="status-icon">{config.icon}</span>}
      <span className="status-label">{config.label}</span>
      {status === "failed" && onRetry && (
        <button className="status-retry" type="button" onClick={onRetry} aria-label="Retry">
          ↻ Retry
        </button>
      )}
    </span>
  );
}
