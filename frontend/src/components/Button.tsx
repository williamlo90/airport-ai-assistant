import type { ComponentPropsWithRef } from 'react';
import styles from './controls.module.css';

type Props = ComponentPropsWithRef<'button'> & {
  variant?: 'primary' | 'secondary' | 'quiet';
  loading?: boolean;
  loadingLabel?: string;
};

export function Button({
  variant = 'primary',
  loading = false,
  loadingLabel,
  disabled,
  children,
  className = '',
  type = 'button',
  ...props
}: Props) {
  return (
    <button
      {...props}
      type={type}
      className={`${styles.button} ${styles[variant]} ${className}`}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
    >
      {loading && loadingLabel ? loadingLabel : children}
    </button>
  );
}
