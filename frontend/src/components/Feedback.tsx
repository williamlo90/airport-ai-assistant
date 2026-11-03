import type { ReactNode, Ref } from 'react';
import styles from './feedback.module.css';

type State = 'loading' | 'empty' | 'error' | 'unavailable' | 'unknown';
export function AsyncState({
  state,
  title,
  message,
  action,
}: {
  state: State;
  title: string;
  message: string;
  action?: ReactNode;
}) {
  return (
    <section
      className={`${styles.state} ${state === 'error' ? styles.danger : ''}`}
      role={state === 'error' ? 'alert' : 'status'}
      aria-busy={state === 'loading' || undefined}
    >
      <h3>{title}</h3>
      <p>{message}</p>
      {action && <div className={styles.actions}>{action}</div>}
    </section>
  );
}
export function StatusLabel({
  children,
  tone = 'neutral',
}: {
  children: ReactNode;
  tone?: 'neutral' | 'positive' | 'warning' | 'danger';
}) {
  return <span className={`${styles.badge} ${styles[tone]}`}>{children}</span>;
}
export function ErrorSummary({
  title,
  errors,
  ref,
}: {
  title: string;
  errors: readonly { fieldId: string; message: string }[];
  ref?: Ref<HTMLDivElement>;
}) {
  if (!errors.length) return null;
  return (
    <div ref={ref} className={styles.errorSummary} role="alert" tabIndex={-1}>
      <h3>{title}</h3>
      <ul>
        {errors.map((error, index) => (
          <li key={`${error.fieldId}-${index}`}>
            <a
              href={`#${error.fieldId}`}
              onClick={(event) => {
                event.preventDefault();
                document.getElementById(error.fieldId)?.focus();
              }}
            >
              {error.message}
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}
