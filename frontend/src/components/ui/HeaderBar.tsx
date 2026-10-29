import { useState, type ReactNode } from "react";
import { RefreshCw } from "lucide-react";
import { Spinner } from "./Spinner";
import { NotificationBell } from "./NotificationBell";
import { Button } from "./primitives/Button";
import { Badge } from "./primitives/Badge";
import type { BadgeVariant } from "./primitives/Badge";

export type HeaderBarStatus = "sync" | "loading" | "error" | "degraded" | "paused" | "unknown";

function statusToBadgeVariant(status: HeaderBarStatus): BadgeVariant {
  switch (status) {
    case "error":
      return "danger";
    case "degraded":
    case "paused":
    case "unknown":
    case "loading":
      return "warning";
    case "sync":
    default:
      return "success";
  }
}

export function HeaderBar({
  title,
  description,
  status,
  errorMessage,
  lastRefreshedAt,
  onRefresh,
  notificationUnread,
  onOpenNotifications,
  rightSlot,
  onShowEnvironment,
}: {
  title: string;
  description?: string;
  status: HeaderBarStatus;
  errorMessage?: string;
  lastRefreshedAt?: string | null;
  onRefresh: () => void;
  notificationUnread?: number;
  onOpenNotifications?: () => void;
  rightSlot?: ReactNode;
  onShowEnvironment?: () => void;
}) {
  const [statusOpen, setStatusOpen] = useState(false);
  const pillText =
    status === "loading"
      ? "同步中…"
      : status === "error"
        ? "数据异常"
        : status === "degraded"
          ? "部分处理异常"
          : status === "paused"
            ? "持续采集已暂停"
            : status === "unknown"
              ? "运行状态待确认"
              : "已连接本地服务";
  return (
    <header className="workspace-header">
      <div className="workspace-header__titles">
        <h1>{title}</h1>
        {description ? <p className="workspace-header__description">{description}</p> : null}
      </div>
      <div className="workspace-header-right">
        {rightSlot}
        {onOpenNotifications && notificationUnread !== undefined ? (
          <NotificationBell unread={notificationUnread} onClick={onOpenNotifications} />
        ) : null}
        <Button
          variant="ghost"
          size="sm"
          icon={RefreshCw}
          onClick={onRefresh}
          aria-label="刷新数据"
          loading={status === "loading"}
        >
          刷新
        </Button>
        <div className="status-pill" role="status" aria-live="polite" onMouseLeave={() => setStatusOpen(false)}>
          <button
            type="button"
            className="status-pill__button"
            aria-label={pillText}
            aria-expanded={statusOpen}
            onClick={() => setStatusOpen((value) => !value)}
            onMouseEnter={() => setStatusOpen(true)}
          >
            <Badge variant={statusToBadgeVariant(status)} className="status-pill__inner">
              {status === "loading" ? <Spinner size={14} /> : null}
              {status === "error" ? <span className="icon warn" aria-hidden="true" /> : null}
              {status !== "loading" && status !== "error" ? <i className="live-dot" aria-hidden="true" /> : null}
              <span className="status-pill__text">{pillText}</span>
            </Badge>
          </button>
          {statusOpen ? (
            <div className="status-pill__popover">
              <ul>
                {(errorMessage ? errorMessage.split("；") : [pillText]).map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
              {onShowEnvironment ? (
                <button
                  type="button"
                  onClick={() => {
                    setStatusOpen(false);
                    onShowEnvironment();
                  }}
                >
                  查看环境
                </button>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>
      {lastRefreshedAt ? <div className="refresh-line">自动刷新: {lastRefreshedAt}</div> : null}
    </header>
  );
}
