import type { ReactElement } from "react";
import { Card, CardHeader, CardTitle, CardActionsBar } from "./primitives/Card";
import { Button } from "./primitives/Button";
import { Badge } from "./primitives/Badge";
import type { BadgeVariant } from "./primitives/Badge";

export type SetupStatus = "pending" | "in_progress" | "done" | "failed";

function statusToBadgeVariant(status: SetupStatus): BadgeVariant {
  switch (status) {
    case "done": return "success";
    case "failed": return "danger";
    case "in_progress": return "warning";
    case "pending":
    default: return "text";
  }
}

export interface SetupCardProps {
  step: number;
  title: string;
  status: SetupStatus;
  primaryLabel?: string;
  onPrimary?: () => void;
  secondaryLabel?: string;
  onSecondary?: () => void;
  error?: string | null;
  details?: ReactElement;
}

export function SetupCard(props: SetupCardProps): ReactElement {
  const { step, title, status, primaryLabel, onPrimary, secondaryLabel, onSecondary, error, details } = props;
  return (
    <div className="setup-card" data-status={status}>
      <Card>
        <CardHeader>
          <span className="setup-card-step">步骤 {step}</span>
          <CardTitle>{title}</CardTitle>
          <Badge variant={statusToBadgeVariant(status)}>{status}</Badge>
        </CardHeader>
        {error ? <p className="setup-card-error">{error}</p> : null}
        {details}
        <CardActionsBar>
          {secondaryLabel && onSecondary ? (
            <Button variant="ghost" size="sm" onClick={onSecondary}>{secondaryLabel}</Button>
          ) : null}
          {primaryLabel && onPrimary ? (
            <Button
              variant="primary"
              size="sm"
              onClick={onPrimary}
              disabled={status === "in_progress"}
            >
              {primaryLabel}
            </Button>
          ) : null}
        </CardActionsBar>
      </Card>
    </div>
  );
}
