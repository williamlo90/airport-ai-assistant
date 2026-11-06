import {
  useEffect,
  useId,
  useMemo,
  useState,
  useSyncExternalStore,
} from 'react';
import type { AuthCoordinator } from '../../app/auth/coordinator';
import { useAuthState } from '../../app/auth/context';
import type { Locale } from '../../locales/messages';
import { Button } from '../../components/Button';
import { errorMessage } from '../../api/errors';
import { citationSource } from './citation-contracts';
import { CitationStore } from './citation-store';
import styles from './chat.module.css';

interface Props {
  auth: AuthCoordinator;
  conversationId: string;
  messageId: string;
  generationEpoch: number;
  locale: Locale;
}
export function MessageCitations(props: Props) {
  const session = useAuthState(props.auth);
  if (session.phase !== 'ready' || session.kind === 'staff') return null;
  return (
    <Disclosure
      key={`${session.boundaryId}:${session.epoch}:${props.conversationId}:${props.messageId}:${props.generationEpoch}`}
      {...props}
    />
  );
}

function Disclosure({ auth, conversationId, messageId, locale }: Props) {
  const store = useMemo(
    () => new CitationStore(auth, conversationId, messageId),
    [auth, conversationId, messageId],
  );
  const state = useSyncExternalStore(store.subscribe, store.getState);
  const panelId = useId();
  const [now, setNow] = useState(Date.now);
  useEffect(() => store.connect(), [store]);
  useEffect(() => {
    if (!state.retryAt || now >= state.retryAt) return;
    const timer = setTimeout(
      () => setNow(Date.now()),
      Math.max(0, Math.min(state.retryAt - Date.now() + 1, 2147483647)),
    );
    return () => clearTimeout(timer);
  }, [state.retryAt, now]);
  const waiting = state.retryAt > now;
  const t = (en: string, id: string) => (locale === 'en' ? en : id);
  const receipt = state.receipt;
  return (
    <div className={styles.citations}>
      <Button
        variant="quiet"
        aria-expanded={state.open}
        aria-controls={panelId}
        onClick={() => {
          if (state.open) store.close();
          else void store.load();
        }}
      >
        {t('Sources', 'Sumber')}{' '}
        <span aria-hidden="true">{state.open ? '−' : '+'}</span>
      </Button>
      <div id={panelId} hidden={!state.open} className={styles.citationPanel}>
        {state.phase === 'loading' && (
          <p role="status">{t('Loading sources…', 'Memuat sumber…')}</p>
        )}
        {state.phase === 'error' && (
          <div>
            <p role="status">
              {waiting
                ? t(
                    'Please wait before requesting sources again.',
                    'Tunggu sebelum meminta sumber lagi.',
                  )
                : state.error
                  ? errorMessage(state.error, locale)
                  : t(
                      'Sources could not be loaded.',
                      'Sumber belum dapat dimuat.',
                    )}
            </p>
            <Button
              variant="secondary"
              disabled={waiting}
              onClick={() => {
                void store.load();
              }}
            >
              {t('Retry sources', 'Coba muat sumber lagi')}
            </Button>
          </div>
        )}
        {receipt && (
          <>
            {receipt.citations.length === 0 ? (
              <p role="status">
                {t(
                  'No source citations were recorded for this message.',
                  'Tidak ada sitasi sumber yang tercatat untuk pesan ini.',
                )}
              </p>
            ) : (
              <>
                <p>
                  {t(
                    'Historical sources used for this answer. Policies may have changed since they were checked.',
                    'Sumber historis yang digunakan untuk jawaban ini. Kebijakan mungkin sudah berubah sejak sumber diperiksa.',
                  )}
                </p>
                {receipt.issued_at && (
                  <p>
                    {t('Sources checked: ', 'Sumber diperiksa: ')}
                    <time dateTime={receipt.issued_at}>
                      {new Intl.DateTimeFormat(
                        locale === 'en' ? 'en-GB' : 'id-ID',
                        {
                          dateStyle: 'medium',
                          timeStyle: 'short',
                          timeZone: 'UTC',
                        },
                      ).format(new Date(receipt.issued_at))}{' '}
                      UTC
                    </time>
                  </p>
                )}
                <ol className={styles.citationList}>
                  {receipt.citations.map((citation) => (
                    <li
                      key={`${citation.version_id}:${citation.generation}:${citation.ordinal}`}
                    >
                      <blockquote>{citation.quote}</blockquote>
                      <p>
                        {citationSource(citation.source_uri) === 'https' ? (
                          <a
                            href={citation.source_uri}
                            target="_blank"
                            rel="noopener noreferrer"
                            referrerPolicy="no-referrer"
                          >
                            {citation.source_uri}
                            <span className={styles.citationLinkHint}>
                              {' '}
                              {t(
                                '(opens in a new tab)',
                                '(dibuka di tab baru)',
                              )}
                            </span>
                          </a>
                        ) : (
                          <span>{citation.source_uri}</span>
                        )}
                      </p>
                    </li>
                  ))}
                </ol>
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}
