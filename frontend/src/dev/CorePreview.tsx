import { useEffect, useId, useRef, useState } from 'react';
import type { PublicSite } from '../app/config/publicSite';
import { CustomerShell, StaffShell } from '../app/shells/Shells';
import { Button } from '../components/Button';
import { Field, TextArea, Select } from '../components/Fields';
import { Dialog } from '../components/Dialog';
import { AsyncState, ErrorSummary, StatusLabel } from '../components/Feedback';
import type { Locale } from '../locales/messages';
import { messages } from '../locales/messages';
import { coreMessages } from '../locales/core';
import { formatMoney, formatTimestamp } from '../locales/format';
import styles from './preview.module.css';

const states = ['loading', 'empty', 'error', 'unavailable', 'unknown'] as const;
type PreviewState = (typeof states)[number];
export default function CorePreview({
  site,
  locale,
  onLanguageChange,
}: {
  site: PublicSite;
  locale: Locale;
  onLanguageChange: () => void;
}) {
  const c = coreMessages[locale];
  const id = useId();
  const [staff, setStaff] = useState(false),
    [reference, setReference] = useState(''),
    [message, setMessage] = useState(''),
    [service, setService] = useState('parking'),
    [open, setOpen] = useState(false),
    [state, setState] = useState<PreviewState>('empty'),
    [checked, setChecked] = useState(false),
    [busy, setBusy] = useState(false);
  const [invalid, setInvalid] = useState<readonly ('reference' | 'message')[]>(
    [],
  );
  const summary = useRef<HTMLDivElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const shellFocus = useRef(false);
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );
  useEffect(() => {
    if (invalid.length) summary.current?.focus();
  }, [invalid]);
  useEffect(() => {
    if (shellFocus.current) {
      document.getElementById(`${id}-${staff ? 'staff' : 'customer'}`)?.focus();
      shellFocus.current = false;
    }
  }, [id, staff]);
  const referenceId = `${id}-reference`,
    messageId = `${id}-message`;
  const errors = invalid.map((field) => ({
    fieldId: field === 'reference' ? referenceId : messageId,
    message: `${field === 'reference' ? c.reference : c.message}: ${c.required}`,
  }));
  const title = c[`${state}Title`],
    body = c[`${state}Body`];
  const common = {
    site,
    skipLabel: c.skip,
    languageLabel: messages[locale].switchLanguage,
    onLanguageChange,
  };
  function switchShell(value: boolean) {
    if (value === staff) return;
    shellFocus.current = true;
    setStaff(value);
  }
  function validate() {
    if (busy) return;
    const next: ('reference' | 'message')[] = [];
    if (!reference.trim()) next.push('reference');
    if (!message.trim()) next.push('message');
    setInvalid(next);
    setChecked(false);
    if (next.length) return;
    setBusy(true);
    timer.current = setTimeout(() => {
      setBusy(false);
      setChecked(true);
      timer.current = null;
    }, 600);
  }
  const content = (
    <div className={styles.content}>
      <div className={styles.intro}>
        <h1>{c.title}</h1>
        <p>{c.intro}</p>
        <div className={styles.row}>
          <Button
            id={`${id}-customer`}
            variant="secondary"
            aria-pressed={!staff}
            onClick={() => switchShell(false)}
          >
            {c.customer}
          </Button>
          <Button
            id={`${id}-staff`}
            variant="secondary"
            aria-pressed={staff}
            onClick={() => switchShell(true)}
          >
            {c.staff}
          </Button>
        </div>
      </div>
      <div className={styles.grid}>
        <section className={styles.panel} aria-labelledby={`${id}-form`}>
          <h2 id={`${id}-form`}>{c.form}</h2>
          <form
            noValidate
            onSubmit={(event) => {
              event.preventDefault();
              validate();
            }}
          >
            <ErrorSummary ref={summary} title={c.errors} errors={errors} />
            <Field
              id={referenceId}
              disabled={busy}
              label={c.reference}
              hint={c.hint}
              required
              value={reference}
              maxLength={80}
              onChange={(event) => {
                setReference(event.target.value);
                setChecked(false);
              }}
              {...(errors.some((e) => e.fieldId === referenceId)
                ? { error: c.required }
                : {})}
            />
            <TextArea
              id={messageId}
              disabled={busy}
              label={c.message}
              required
              value={message}
              maxLength={4000}
              onChange={(event) => {
                setMessage(event.target.value);
                setChecked(false);
              }}
              {...(errors.some((e) => e.fieldId === messageId)
                ? { error: c.required }
                : {})}
            />
            <Select
              label={c.service}
              disabled={busy}
              value={service}
              onChange={(event) => setService(event.target.value)}
            >
              <option value="parking">{c.parking}</option>
              <option value="lounge">{c.lounge}</option>
            </Select>
            <div className={styles.row}>
              <Button type="submit" loading={busy} loadingLabel={c.validating}>
                {c.validate}
              </Button>
              <Button variant="secondary" onClick={() => setOpen(true)}>
                {c.review}
              </Button>
            </div>
            {checked && <p role="status">{c.valid}</p>}
          </form>
        </section>
        <div className={styles.stack}>
          <section className={styles.panel}>
            <h2>{c.states}</h2>
            <Select
              label={c.chooseState}
              value={state}
              onChange={(event) => {
                const value = event.target.value;
                if (states.some((item) => item === value))
                  setState(value as PreviewState);
              }}
            >
              {states.map((value) => (
                <option key={value} value={value}>
                  {c[value]}
                </option>
              ))}
            </Select>
            <AsyncState
              state={state}
              title={title}
              message={body}
              {...(state === 'error' ||
              state === 'unavailable' ||
              state === 'unknown'
                ? {
                    action: (
                      <Button
                        variant="secondary"
                        onClick={() => setState('loading')}
                      >
                        {c.retry}
                      </Button>
                    ),
                  }
                : {})}
            />
          </section>
          <section className={styles.panel}>
            <h2>{c.badges}</h2>
            <div className={styles.row}>
              <StatusLabel>{c.neutral}</StatusLabel>
              <StatusLabel tone="positive">{c.positive}</StatusLabel>
              <StatusLabel tone="warning">{c.warning}</StatusLabel>
              <StatusLabel tone="danger">{c.danger}</StatusLabel>
            </div>
            <h3>{c.formatters}</h3>
            <p>
              {formatMoney(12345, 'USD', 2, locale)} ·{' '}
              {formatTimestamp('2026-11-03T08:00:00Z', locale, 'UTC')}
            </p>
            <p className={styles.muted}>{c.formatHint}</p>
          </section>
        </div>
      </div>
      <Dialog
        open={open}
        title={c.dialogTitle}
        description={c.dialogDescription}
        closeLabel={c.close}
        onClose={() => setOpen(false)}
        actions={
          <>
            <Button variant="secondary" onClick={() => setOpen(false)}>
              {c.cancel}
            </Button>
            <Button onClick={() => setOpen(false)}>{c.done}</Button>
          </>
        }
      >
        <dl className={styles.review}>
          <dt>{c.reference}</dt>
          <dd>{reference || c.missing}</dd>
          <dt>{c.message}</dt>
          <dd>{message || c.missing}</dd>
          <dt>{c.service}</dt>
          <dd>{service === 'parking' ? c.parking : c.lounge}</dd>
        </dl>
      </Dialog>
    </div>
  );
  return staff ? (
    <StaffShell
      {...common}
      navigationLabel={c.navigation}
      navigation={
        <>
          <Button variant="quiet" onClick={() => setState('empty')}>
            {c.queue}
          </Button>
          <Button variant="quiet" onClick={() => setOpen(true)}>
            {c.case}
          </Button>
        </>
      }
    >
      {content}
    </StaffShell>
  ) : (
    <CustomerShell {...common}>{content}</CustomerShell>
  );
}
