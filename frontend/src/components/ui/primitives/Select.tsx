import { forwardRef, useId, type SelectHTMLAttributes, type ReactNode } from "react";
import { cx } from "../../../lib/format";

export interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label?: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  required?: boolean;
  children: ReactNode;
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { label, hint, error, required, id: idProp, className, children, ...rest },
  ref,
) {
  const generatedId = useId();
  const id = idProp ?? generatedId;
  const fieldClasses = cx("input-field", { "input-field--error": Boolean(error) });
  return (
    <div className={fieldClasses}>
      {label ? (
        <label className={`input-field__label${required ? " input-field__label--required" : ""}`} htmlFor={id}>
          {label}
        </label>
      ) : null}
      <select ref={ref} id={id} required={required} className={`input-field__select ${className ?? ""}`.trim()} {...rest}>
        {children}
      </select>
      {error ? <span className="input-field__error" role="alert">{error}</span> : null}
      {!error && hint ? <span className="input-field__hint">{hint}</span> : null}
    </div>
  );
});
