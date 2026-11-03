import { useEffect, useId, useRef } from 'react';
import type { ReactNode } from 'react';
import styles from './feedback.module.css';

interface Props {
  open: boolean;
  title: string;
  description?: string;
  closeLabel: string;
  onClose: () => void;
  children?: ReactNode;
  actions?: ReactNode;
}
export function Dialog({
  open,
  title,
  description,
  closeLabel,
  onClose,
  children,
  actions,
}: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const id = useId();
  useEffect(() => {
    const node = dialog.current;
    if (!node) return;
    if (open && !node.open) node.showModal();
    else if (!open && node.open) node.close();
  }, [open]);
  return (
    <dialog
      ref={dialog}
      className={styles.dialog}
      aria-labelledby={`${id}-title`}
      aria-describedby={description ? `${id}-description` : undefined}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClose={() => {
        if (open) onClose();
      }}
    >
      <button
        type="button"
        className={styles.close}
        aria-label={closeLabel}
        onClick={onClose}
      >
        <span aria-hidden="true">×</span>
      </button>
      <h2 id={`${id}-title`}>{title}</h2>
      {description && (
        <p className={styles.description} id={`${id}-description`}>
          {description}
        </p>
      )}
      {children}
      {actions && <div className={styles.actions}>{actions}</div>}
    </dialog>
  );
}
