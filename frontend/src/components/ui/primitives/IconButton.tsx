import { forwardRef, type ButtonHTMLAttributes } from "react";
import type { LucideIcon } from "lucide-react";
import { cx } from "../../../lib/format";

export type IconButtonSize = "sm" | "md" | "lg";
export type IconButtonVariant = "default" | "danger";

export interface IconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "type"> {
  icon: LucideIcon;
  size?: IconButtonSize;
  variant?: IconButtonVariant;
  /** Required for accessibility — describes what the button does. */
  "aria-label": string;
  type?: "button" | "submit" | "reset";
}

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { icon: Icon, size = "sm", variant = "default", className, type = "button", ...rest },
  ref,
) {
  const classes = cx(
    "icon-btn",
    { "icon-btn--md": size === "md", "icon-btn--lg": size === "lg", "icon-btn--danger": variant === "danger" },
    className,
  );
  const iconSize = size === "lg" ? 18 : size === "md" ? 16 : 14;
  return (
    <button ref={ref} type={type} className={classes} {...rest}>
      <Icon size={iconSize} aria-hidden="true" />
    </button>
  );
});
