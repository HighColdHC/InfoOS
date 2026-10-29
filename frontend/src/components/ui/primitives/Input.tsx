import { forwardRef, useId, type InputHTMLAttributes, type ReactNode, type TextareaHTMLAttributes } from "react";
import { cx } from "../../../lib/format";

type CommonProps = {
  label?: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  required?: boolean;
};

export interface InputProps extends CommonProps, Omit<InputHTMLAttributes<HTMLInputElement>, "required"> {
  textarea?: false;
}
export interface TextareaProps extends CommonProps, Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "required"> {
  textarea: true;
}
export type AnyInputProps = InputProps | TextareaProps;

export const Input = forwardRef<HTMLInputElement | HTMLTextAreaElement, AnyInputProps>(function Input(
  { label, hint, error, required, textarea = false, id: idProp, className, ...rest },
  ref,
) {
  const generatedId = useId();
  const id = idProp ?? generatedId;
  const fieldClasses = cx("input-field", { "input-field--error": Boolean(error) });
  const inputClasses = cx(textarea ? "input-field__textarea" : "input-field__input", className);
  return (
    <div className={fieldClasses}>
      {label ? (
        <label className={`input-field__label${required ? " input-field__label--required" : ""}`} htmlFor={id}>
          {label}
        </label>
      ) : null}
      {textarea ? (
        <textarea ref={ref as React.Ref<HTMLTextAreaElement>} id={id} required={required} className={inputClasses} {...(rest as TextareaHTMLAttributes<HTMLTextAreaElement>)} />
      ) : (
        <input ref={ref as React.Ref<HTMLInputElement>} id={id} required={required} className={inputClasses} {...(rest as InputHTMLAttributes<HTMLInputElement>)} />
      )}
      {error ? <span className="input-field__error" role="alert">{error}</span> : null}
      {!error && hint ? <span className="input-field__hint">{hint}</span> : null}
    </div>
  );
});
