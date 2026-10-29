import type { ReactNode } from "react";

type Option<T extends string> = { label: string; value: T; disabled?: boolean; icon?: ReactNode };

type Props<T extends string> = {
  options: Option<T>[];
  value: T;
  onChange: (value: T) => void;
  size?: "sm" | "md";
  disabled?: boolean;
  label: string;
  className?: string;
  tabIdPrefix?: string;
  panelIdPrefix?: string;
};

export function SegmentedControl<T extends string>({
  options, value, onChange, size = "md", disabled = false, label, className, tabIdPrefix, panelIdPrefix,
}: Props<T>) {
  const tabs = Boolean(tabIdPrefix && panelIdPrefix);
  return (
    <div className={`segmented-control segmented-control--${size}${className ? ` ${className}` : ""}`} role={tabs ? "tablist" : "radiogroup"} aria-label={label}>
      {options.map((option) => (
        <button
          key={option.value}
          id={tabs ? `${tabIdPrefix}${option.value}` : undefined}
          type="button"
          role={tabs ? "tab" : "radio"}
          aria-selected={tabs ? value === option.value : undefined}
          aria-checked={tabs ? undefined : value === option.value}
          aria-controls={tabs ? `${panelIdPrefix}${option.value}` : undefined}
          disabled={disabled || option.disabled}
          onClick={() => onChange(option.value)}
        >
          {option.icon ? <span className="segmented-control__icon" aria-hidden="true">{option.icon}</span> : null}
          {option.label}
        </button>
      ))}
    </div>
  );
}
