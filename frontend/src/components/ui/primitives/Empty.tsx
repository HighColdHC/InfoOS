import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { Inbox, BookOpen, Library as LibraryIcon, Rss, Activity, Sparkles } from "lucide-react";
import { Button } from "./Button";

export type EmptyIllustration = "brief" | "flow" | "library" | "source" | "runs" | "topic";

const ICON_MAP: Record<EmptyIllustration, LucideIcon> = {
  brief: Inbox,
  flow: Activity,
  library: LibraryIcon,
  source: Rss,
  runs: Activity,
  topic: BookOpen,
};

export interface EmptyProps {
  text: string;
  illustration?: EmptyIllustration;
  cta?: { label: string; onClick: () => void };
  children?: ReactNode;
}

export function Empty({ text, illustration, cta, children }: EmptyProps) {
  const Icon = illustration ? ICON_MAP[illustration] : Sparkles;
  return (
    <div className="empty" role="status">
      <div className="empty__icon" aria-hidden="true">
        <Icon size={32} />
      </div>
      <p className="empty__description">{text}</p>
      {cta ? (
        <div className="empty__primary">
          <Button variant="primary" size="md" onClick={cta.onClick}>
            {cta.label}
          </Button>
        </div>
      ) : null}
      {children}
    </div>
  );
}
