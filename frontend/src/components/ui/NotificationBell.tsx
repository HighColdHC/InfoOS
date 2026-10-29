// NotificationBell — header button that shows a numeric badge for
// unread notifications. Renders inside HeaderBar's right slot; clicking
// it triggers the parent to open the NotificationDrawer.

import { Bell } from "lucide-react";
import type { ReactElement } from "react";
import { IconButton } from "./primitives/IconButton";
import { Badge } from "./primitives/Badge";

export function NotificationBell({ unread, onClick }: {
  unread: number;
  onClick: () => void;
}): ReactElement {
  const label = unread > 0 ? `通知 ${unread} 条未读` : "通知";
  return (
    <span className="notification-bell-wrap">
      <IconButton icon={Bell} size="sm" onClick={onClick} aria-label={label} />
      {unread > 0 ? <Badge count={unread} variant="count" /> : null}
    </span>
  );
}