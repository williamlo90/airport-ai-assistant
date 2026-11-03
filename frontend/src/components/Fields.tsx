import { useId } from 'react';
import type { ComponentPropsWithRef, ReactNode } from 'react';
import styles from './controls.module.css';

interface Shared {
  label: string;
  hint?: string;
  error?: string;
  id?: string;
}
function Frame({
  label,
  hint,
  error,
  id,
  children,
}: Shared & { id: string; children: ReactNode }) {
  return (
    <div className={styles.field}>
      <label htmlFor={id}>{label}</label>
      {hint && (
        <p className={styles.hint} id={`${id}-hint`}>
          {hint}
        </p>
      )}
      {children}
      {error && (
        <p className={styles.error} id={`${id}-error`}>
          {error}
        </p>
      )}
    </div>
  );
}
function describedBy(
  id: string,
  hint: string | undefined,
  error: string | undefined,
  custom: string | undefined,
) {
  return (
    [hint ? `${id}-hint` : undefined, error ? `${id}-error` : undefined, custom]
      .filter(Boolean)
      .join(' ') || undefined
  );
}
export function Field({
  label,
  hint,
  error,
  id,
  ...props
}: Shared & ComponentPropsWithRef<'input'>) {
  const generated = useId();
  const inputId = id ?? generated;
  return (
    <Frame
      id={inputId}
      label={label}
      {...(hint ? { hint } : {})}
      {...(error ? { error } : {})}
    >
      <input
        {...props}
        id={inputId}
        className={`${styles.control} ${props.className ?? ''}`}
        aria-invalid={error ? true : props['aria-invalid']}
        aria-describedby={describedBy(
          inputId,
          hint,
          error,
          props['aria-describedby'],
        )}
      />
    </Frame>
  );
}
export function TextArea({
  label,
  hint,
  error,
  id,
  ...props
}: Shared & ComponentPropsWithRef<'textarea'>) {
  const generated = useId();
  const inputId = id ?? generated;
  return (
    <Frame
      id={inputId}
      label={label}
      {...(hint ? { hint } : {})}
      {...(error ? { error } : {})}
    >
      <textarea
        {...props}
        id={inputId}
        className={`${styles.control} ${styles.textarea} ${props.className ?? ''}`}
        aria-invalid={error ? true : props['aria-invalid']}
        aria-describedby={describedBy(
          inputId,
          hint,
          error,
          props['aria-describedby'],
        )}
      />
    </Frame>
  );
}
export function Select({
  label,
  hint,
  error,
  id,
  children,
  ...props
}: Shared & ComponentPropsWithRef<'select'>) {
  const generated = useId();
  const inputId = id ?? generated;
  return (
    <Frame
      id={inputId}
      label={label}
      {...(hint ? { hint } : {})}
      {...(error ? { error } : {})}
    >
      <select
        {...props}
        id={inputId}
        className={`${styles.control} ${props.className ?? ''}`}
        aria-invalid={error ? true : props['aria-invalid']}
        aria-describedby={describedBy(
          inputId,
          hint,
          error,
          props['aria-describedby'],
        )}
      >
        {children}
      </select>
    </Frame>
  );
}
