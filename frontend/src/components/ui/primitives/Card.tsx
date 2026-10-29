import type { ReactNode } from "react";
import { cx } from "../../../lib/format";

export interface CardProps {
  children: ReactNode;
  className?: string;
  subtle?: boolean;
  padded?: boolean;
}

export function CardHeader({ children, className }: { children: ReactNode; className?: string }) {
  return <header className={`card__header ${className ?? ""}`.trim()}>{children}</header>;
}
export function CardTitle({ children }: { children: ReactNode }) {
  return <h2 className="card__title">{children}</h2>;
}
export function CardSubtitle({ children }: { children: ReactNode }) {
  return <p className="card__subtitle">{children}</p>;
}
export function CardActions({ children }: { children: ReactNode }) {
  return <div className="card__actions">{children}</div>;
}
export function CardBody({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={`card__body ${className ?? ""}`.trim()}>{children}</div>;
}
export function CardActionsBar({ children }: { children: ReactNode }) {
  return <div className="card__actions-bar">{children}</div>;
}

export function Card({ children, className, subtle = false, padded = true }: CardProps) {
  const classes = cx("card", { "card--subtle": subtle, "card--padded": padded }, className);
  return <section className={classes}>{children}</section>;
}
