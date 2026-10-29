import type { ReactNode } from "react";
import { cx } from "../../../lib/format";

export type BadgeVariant = "count" | "dot" | "text" | "success" | "warning" | "danger";

export interface BadgeProps {
  count?: number;
  variant?: BadgeVariant;
  children?: ReactNode;
  className?: string;
  "aria-label"?: string;
}

export function Badge({ count, variant = "count", children, className, ...rest }: BadgeProps) {
  if (variant === "count" && !count) return null;
  const display = variant === "count" ? (count! > 99 ? "99+" : count) : children;
  const variantClass = `badge--${variant}`;
  const classes = cx("badge", variantClass, className);
  return (
    <span className={classes} role={variant === "count" ? "status" : undefined} aria-label={rest["aria-label"]}>
      {display}
    </span>
  );
}
