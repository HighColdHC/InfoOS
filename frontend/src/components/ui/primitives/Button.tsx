import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { Loader2 } from "lucide-react";
import { cx } from "../../../lib/format";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "link";
export type ButtonSize = "sm" | "md" | "lg";

export interface ButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "type"> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  icon?: LucideIcon;
  iconPosition?: "before" | "after";
  loading?: boolean;
  fullWidth?: boolean;
  type?: "button" | "submit" | "reset";
  children?: ReactNode;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", icon: Icon, iconPosition = "before", loading = false, fullWidth = false, type = "button", children, disabled, className, ...rest },
  ref,
) {
  const classes = cx(
    "btn",
    `btn--${variant}`,
    `btn--${size}`,
    { "btn--full": fullWidth, "btn--loading": loading },
    className,
  );
  const IconEl = Icon ? <Icon size={size === "sm" ? 12 : 14} aria-hidden="true" className="btn__icon" /> : null;
  return (
    <button ref={ref} type={type} disabled={disabled || loading} className={classes} aria-busy={loading || undefined} {...rest}>
      {loading ? <Loader2 size={14} aria-hidden="true" className="btn__icon btn__icon--spinner" /> : null}
      {IconEl && iconPosition === "before" ? IconEl : null}
      {children}
      {IconEl && iconPosition === "after" ? <span className="btn__icon btn__icon--after">{IconEl}</span> : null}
    </button>
  );
});
