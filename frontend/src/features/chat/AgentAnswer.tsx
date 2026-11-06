import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { Link } from 'react-router';
import { Button } from '../../components/Button';
import { localizedTarget } from '../../app/routing/policy';
import type { Locale } from '../../locales/messages';
import type { ChatStore } from './store';
import type { PublicService } from './agent-contracts';
import { privateReferences } from './agent-contracts';
import styles from './chat.module.css';

export function AgentAnswer({
  store,
  conversationId,
  routeKey,
  locale,
}: {
  store: ChatStore;
  conversationId: string;
  routeKey: string;
  locale: Locale;
}) {
  const state = useSyncExternalStore(
    store.agent.subscribe,
    store.agent.getState,
  );
  const chat = useSyncExternalStore(store.subscribe, store.getState);
  const auth = useSyncExternalStore(store.auth.subscribe, store.auth.getState);
  const history = useSyncExternalStore(
    store.timeline.subscribe,
    store.timeline.getState,
  );
  const [service, setService] = useState<PublicService>('general');
  const bookingInput = useRef<HTMLInputElement>(null);
  const transactionInput = useRef<HTMLInputElement>(null);
  const [referenceError, setReferenceError] = useState(false);
  const [now, setNow] = useState(Date.now);
  useEffect(
    () => store.agent.attach(conversationId, routeKey),
    [store, conversationId, routeKey, auth.epoch, auth.phase],
  );
  useEffect(() => {
    if (!state.retryAt || now >= state.retryAt) return;
    const timer = setTimeout(
      () => setNow(Date.now()),
      Math.max(0, Math.min(state.retryAt - Date.now() + 1, 2147483647)),
    );
    return () => clearTimeout(timer);
  }, [state.retryAt, now]);
  const t = (en: string, id: string) => (locale === 'en' ? en : id);
  const visible = state.conversationId === conversationId;
  const phase = visible ? state.phase : 'idle';
  const waiting = visible && state.retryAt > now;
  const mode = history.id === conversationId ? history.snapshot?.mode : null;
  const basisVisible =
    chat.meta?.conversation_id === conversationId &&
    chat.meta.language === locale &&
    mode === 'ai';
  const canAsk = basisVisible && store.agent.canAsk() && !waiting;
  const privateVisible =
    state.privateAvailable &&
    canAsk &&
    auth.phase === 'ready' &&
    auth.kind === 'customer' &&
    history.phase === 'ready';
  // Subscribe to current chat/history above so availability follows authorized
  // reads, not an effect that might restart work after remount/reconnect.
  const copy = {
    idle: '',
    starting: t('Preparing your question…', 'Menyiapkan pertanyaan Anda…'),
    answering: t(
      'Looking for an answer… You can still receive conversation updates.',
      'Mencari jawaban… Anda tetap dapat menerima pembaruan percakapan.',
    ),
    prepared: t(
      'Your question is ready. Choose Ask AI to continue.',
      'Pertanyaan Anda siap. Pilih Tanya AI untuk melanjutkan.',
    ),
    'unknown-start': t(
      'Your question may be ready, but this has not been confirmed. You can check the same request without asking the AI again.',
      'Pertanyaan Anda mungkin sudah siap, tetapi belum dipastikan. Anda dapat memeriksa permintaan yang sama tanpa meminta jawaban AI lagi.',
    ),
    'unknown-answer': t(
      'The answer is not confirmed. Check saved conversation updates or get help from support. We will not ask the AI again automatically.',
      'Jawaban belum dipastikan. Periksa pembaruan percakapan tersimpan atau minta bantuan petugas. Kami tidak akan meminta jawaban AI lagi secara otomatis.',
    ),
    unavailable: t(
      'AI answers are temporarily unavailable. Your saved question is still available for a support request.',
      'Jawaban AI sementara belum tersedia. Pertanyaan tersimpan Anda tetap dapat dibawa ke permintaan bantuan.',
    ),
    published: t(
      'An answer was saved. It appears in the conversation once saved updates are loaded.',
      'Jawaban sudah tersimpan. Jawaban muncul dalam percakapan setelah pembaruan tersimpan dimuat.',
    ),
    discarded: t(
      'This answer was not added because the conversation changed. Check the latest updates before continuing.',
      'Jawaban ini tidak ditambahkan karena percakapan berubah. Periksa pembaruan terbaru sebelum melanjutkan.',
    ),
    review: t(
      'An AI answer could not be completed. You can add details or review a support request using your saved question.',
      'Jawaban AI belum dapat diselesaikan. Anda dapat menambah detail atau meninjau permintaan bantuan menggunakan pertanyaan tersimpan.',
    ),
    draft: t(
      'A request is ready for your review. Nothing is submitted until you confirm.',
      'Permintaan siap ditinjau. Belum ada pengajuan sampai Anda mengonfirmasi.',
    ),
  }[phase];
  return (
    <section
      className={styles.agentHelp}
      aria-label={t('AI help', 'Bantuan AI')}
    >
      {copy && <p role="status">{copy}</p>}
      {waiting && (
        <p role="status">
          {t(
            'Please wait before trying again.',
            'Tunggu sebelum mencoba lagi.',
          )}
        </p>
      )}
      {store.agent.canAsk() && (
        <>
          <label>
            {t('Help with', 'Bantuan tentang')}{' '}
            <select
              value={service}
              disabled={!canAsk}
              onChange={(event) =>
                setService(event.target.value as PublicService)
              }
            >
              <option value="general">
                {t('Airport information', 'Informasi bandara')}
              </option>
              <option value="parking">{t('Parking', 'Parkir')}</option>
              <option value="lounge">Lounge</option>
            </select>
          </label>{' '}
          {privateVisible && (
            <fieldset
              key={`${conversationId}:${routeKey}:${auth.epoch}`}
              disabled={!canAsk}
            >
              <legend>
                {t(
                  'Check a booking or payment',
                  'Periksa booking atau pembayaran',
                )}
              </legend>
              <p>
                {t(
                  'Optional: enter references already mentioned in your saved question. We check records and help with next steps; bookings and payments are not changed.',
                  'Opsional: masukkan referensi yang sudah disebutkan dalam pertanyaan tersimpan. Kami memeriksa catatan dan membantu langkah berikutnya; booking dan pembayaran tidak diubah.',
                )}
              </p>
              <label>
                {t('Booking reference', 'Referensi booking')}
                <input
                  ref={bookingInput}
                  maxLength={80}
                  autoComplete="off"
                  spellCheck={false}
                  onChange={() => {
                    setReferenceError(false);
                  }}
                  aria-invalid={referenceError || undefined}
                />
              </label>
              <label>
                {t('Payment reference', 'Referensi pembayaran')}
                <input
                  ref={transactionInput}
                  maxLength={80}
                  autoComplete="off"
                  spellCheck={false}
                  onChange={() => {
                    setReferenceError(false);
                  }}
                  aria-invalid={referenceError || undefined}
                />
              </label>
              {referenceError && (
                <p role="alert">
                  {t(
                    'Use letters, numbers, hyphens or underscores for the reference.',
                    'Gunakan huruf, angka, tanda hubung atau garis bawah untuk referensi.',
                  )}
                </p>
              )}
            </fieldset>
          )}
          <Button
            disabled={!canAsk}
            onClick={() => {
              const booking = bookingInput.current?.value ?? '';
              const transaction = transactionInput.current?.value ?? '';
              if (privateVisible && (booking.trim() || transaction.trim())) {
                const refs = privateReferences.safeParse({
                  booking_references: booking.trim() ? [booking.trim()] : [],
                  transaction_references: transaction.trim()
                    ? [transaction.trim()]
                    : [],
                });
                if (!refs.success) {
                  setReferenceError(true);
                  return;
                }
                if (bookingInput.current) bookingInput.current.value = '';
                if (transactionInput.current)
                  transactionInput.current.value = '';
                setReferenceError(false);
                void store.agent.ask(service, refs.data);
              } else void store.agent.ask(service);
            }}
          >
            {t('Ask AI', 'Tanya AI')}
          </Button>
        </>
      )}
      {basisVisible &&
        store.agent.canRecover() &&
        !['unknown-answer', 'review'].includes(phase) && (
          <Button
            variant="secondary"
            disabled={waiting}
            onClick={() => {
              void (phase === 'prepared'
                ? store.agent.continuePrepared()
                : store.agent.recoverPrepared());
            }}
          >
            {phase === 'prepared'
              ? t('Ask AI', 'Tanya AI')
              : t('Check question', 'Periksa pertanyaan')}
          </Button>
        )}
      {visible &&
        ['unknown-answer', 'discarded', 'published', 'review'].includes(
          phase,
        ) && (
          <Button
            variant="secondary"
            disabled={store.agent.busy}
            onClick={() => store.agent.refresh()}
          >
            {t('Check saved updates', 'Periksa pembaruan tersimpan')}
          </Button>
        )}
      {basisVisible && visible && state.outcome?.state === 'draft' && (
        <Link
          to={localizedTarget(
            '/help/submissions/' + state.outcome.draft_id,
            locale,
          )}
        >
          {t('Review support request', 'Tinjau permintaan bantuan')}
        </Link>
      )}
      {basisVisible && phase === 'unknown-answer' && (
        <Button
          variant="secondary"
          disabled={waiting}
          onClick={() => {
            void store.agent.checkOutcome();
          }}
        >
          {t('Check answer status', 'Periksa status jawaban')}
        </Button>
      )}
      {visible &&
        ['unknown-answer', 'unavailable', 'review'].includes(phase) && (
          <p>
            {t(
              'Your saved question can be carried into a support request for you to review and confirm. ',
              'Pertanyaan tersimpan Anda dapat dibawa ke permintaan bantuan untuk Anda tinjau dan konfirmasi. ',
            )}
            <Link
              to={localizedTarget(
                '/help/conversations/' + conversationId + '?topic=contact',
                locale,
              )}
            >
              {t('Get help', 'Minta bantuan')}
            </Link>
          </p>
        )}
    </section>
  );
}
