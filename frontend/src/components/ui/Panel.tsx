// Panel — compatibility shim that wraps the Card primitive while
// preserving the legacy <Panel title="..." subtitle="..." actions={...}>
// API used by the existing modules (BriefModule, SourcesModule,
// SettingsModule, App.tsx). The Card primitive is a lower-level
// surface that expects CardHeader/CardTitle/CardSubtitle/CardActions/
// CardBody to be composed by the caller.

import type { ReactNode } from "react";
import {
  Card,
  CardHeader,
  CardTitle,
  CardSubtitle,
  CardActions,
  CardBody,
  CardActionsBar,
} from "./primitives/Card";

export interface PanelProps {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
  className?: string;
  subtle?: boolean;
  padded?: boolean;
}

export function Panel({
  title,
  subtitle,
  actions,
  children,
  className,
  subtle,
  padded,
}: PanelProps) {
  return (
    <Card className={className} subtle={subtle} padded={padded}>
      {title || subtitle || actions ? (
        <CardHeader>
          <div>
            {title ? <CardTitle>{title}</CardTitle> : null}
            {subtitle ? <CardSubtitle>{subtitle}</CardSubtitle> : null}
          </div>
          {actions ? <CardActions>{actions}</CardActions> : null}
        </CardHeader>
      ) : null}
      {children ? <CardBody>{children}</CardBody> : null}
    </Card>
  );
}

// Re-export the Card subcomponents under their legacy names for callers
// that want to compose panels by hand.
export {
  CardHeader as PanelHeader,
  CardTitle as PanelTitle,
  CardSubtitle as PanelSubtitle,
  CardActions as PanelActions,
  CardBody as PanelBody,
  CardActionsBar as PanelActionsBar,
};
