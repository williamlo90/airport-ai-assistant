import {
  useEffect,
  useId,
  useMemo,
  useState,
  useSyncExternalStore,
} from 'react';
import { useRouting } from '../../app/routing/context';
import { useUnsavedChanges } from '../../app/routing/draftState';
import { Button } from '../../components/Button';
import { errorMessage } from '../../api/errors';
import type { StaffQueueStore } from './queue-store';
import { StaffCommunicationStore } from './communication-store';
import styles from './actions.module.css';

export function StaffCommunication({
  queue,
  caseId,
  disabled = false,
  onLockChange,
}: {
  queue: StaffQueueStore;
  caseId: string;
  disabled?: boolean;
  onLockChange: (locked: boolean) => void;
}) {
  const store = useMemo(
    () => new StaffCommunicationStore(queue, caseId),
    [queue, caseId],
  );
  const state = useSyncExternalStore(store.subscribe, store.getState);
  const q = useSyncExternalStore(queue.subscribe, queue.getState);
  const { locale } = useRouting();
  const t = (en: string, id: string) => (locale === 'en' ? en : id);
  const id = useId(),
    [now, setNow] = useState(Date.now);
  useEffect(() => store.connect(), [store]);
  useEffect(() => {
    const current = store.getState();
    if (
      current.available &&
      q.detailPhase === 'ready' &&
      current.phase === 'idle' &&
      !current.history &&
      !current.pending
    )
      void store.load();
  }, [store, state.available, q.detailPhase, q.detail?.version]);
  const dirty = store.dirty();
  useUnsavedChanges(dirty, store.discard);
  useEffect(() => {
    onLockChange(dirty);
    return () => onLockChange(false);
  }, [dirty, onLockChange]);
  useEffect(() => {
    if (state.retryAt <= Date.now()) return;
    const timer = setTimeout(
      () => setNow(Date.now()),
      Math.min(2147483647, Math.max(0, state.retryAt - Date.now() + 1)),
    );
    return () => clearTimeout(timer);
  }, [state.retryAt, now]);
  const busy = ['loading', 'sending', 'checking'].includes(state.phase);
  const waiting = state.retryAt > now;
  const locked = disabled || busy || waiting;
  const canReply = store.canReply();
  const draft = state.draft;
  const date = (value: string) =>
    new Intl.DateTimeFormat(locale === 'en' ? 'en-GB' : 'id-ID', {
      dateStyle: 'medium',
      timeStyle: 'short',
      timeZone: 'UTC',
    }).format(new Date(value)) + ' UTC';
  return (
    <section
      className={styles.actions}
      aria-label={t('Customer conversation', 'Percakapan pelanggan')}
    >
      <h3>{t('Customer conversation', 'Percakapan pelanggan')}</h3>
      {state.notice === 'saved' ? (
        <p role="status" className={styles.notice}>
          {state.channel === 'email'
            ? t(
                'Reply saved and queued for email. Check Email activity for delivery status.',
                'Balasan disimpan dan masuk antrean email. Periksa status pengiriman di Aktivitas email.',
              )
            : t(
                'Reply saved to the conversation.',
                'Balasan disimpan ke percakapan.',
              )}
        </p>
      ) : null}
      {state.notice === 'accepted' ? (
        <p role="status">
          {t(
            'You are now handling this conversation.',
            'Anda sekarang menangani percakapan ini.',
          )}
        </p>
      ) : null}
      {state.notice === 'changed' ? (
        <p role="alert">
          {t(
            'The case changed. Refresh the case and review its current context.',
            'Kasus berubah. Muat ulang kasus dan tinjau konteks terkininya.',
          )}
        </p>
      ) : null}
      {state.error && !state.pending ? (
        <p role="alert">{errorMessage(state.error, locale)}</p>
      ) : null}
      {state.pending ? (
        <div role="status" className={styles.notice}>
          <p>
            {t(
              'The result is not confirmed. Checking will only read the saved result; it will not send again.',
              'Hasil belum dipastikan. Pemeriksaan hanya membaca hasil tersimpan dan tidak mengirim ulang.',
            )}
          </p>
          <Button disabled={locked} onClick={() => void store.recover()}>
            {t('Check saved result', 'Periksa hasil tersimpan')}
          </Button>
        </div>
      ) : null}
      {!state.available ? (
        <p role="status">
          {t(
            'Current case access must be checked before showing the conversation.',
            'Akses kasus terkini perlu diperiksa sebelum percakapan ditampilkan.',
          )}
        </p>
      ) : null}
      <div className={styles.row}>
        <Button
          variant="secondary"
          disabled={locked || q.detailPhase !== 'ready'}
          onClick={() => void store.load()}
        >
          {t('Refresh messages', 'Muat ulang pesan')}
        </Button>
        <Button
          variant="quiet"
          disabled={locked || state.historyPage <= 1}
          onClick={() => void store.newer()}
        >
          {t('Newer messages', 'Pesan lebih baru')}
        </Button>
        <Button
          variant="quiet"
          disabled={locked || !state.history?.has_more}
          onClick={() => void store.older()}
        >
          {t('Older messages', 'Pesan lebih lama')}
        </Button>
      </div>
      {state.phase === 'loading' ? (
        <p role="status">{t('Loading messages…', 'Memuat pesan…')}</p>
      ) : null}
      {state.available && state.history ? (
        <>
          <p className={styles.help}>
            {state.historyPage === 1
              ? t(
                  'Showing the most recent messages.',
                  'Menampilkan pesan terbaru.',
                )
              : t(
                  'Showing an earlier page of messages.',
                  'Menampilkan halaman pesan sebelumnya.',
                )}
          </p>
          {state.history.items.length ? (
            <ol
              className={styles.messages}
              aria-label={t('Published messages', 'Pesan yang dipublikasikan')}
            >
              {state.history.items.map((message) => (
                <li key={message.id}>
                  <strong>
                    {message.author_type === 'staff'
                      ? t('Staff', 'Petugas')
                      : message.author_type === 'ai'
                        ? t('AI assistant', 'Asisten AI')
                        : t('Customer', 'Pelanggan')}
                  </strong>
                  <span className={styles.help}>
                    {' '}
                    · {message.channel === 'email' ? 'Email' : 'Chat'} ·{' '}
                    {date(message.created_at)}
                  </span>
                  <p className={styles.preserve}>{message.body}</p>
                </li>
              ))}
            </ol>
          ) : (
            <p>
              {t(
                'No published messages are available on this page.',
                'Tidak ada pesan yang dipublikasikan di halaman ini.',
              )}
            </p>
          )}
        </>
      ) : null}
      {state.available && state.chat?.mode === 'waiting_staff' ? (
        <div className={styles.form}>
          <p>
            {t(
              'The customer requested a live conversation. Accept it before replying.',
              'Pelanggan meminta percakapan langsung. Terima sebelum membalas.',
            )}
          </p>
          <Button
            disabled={locked || !!state.pending}
            onClick={() => void store.accept()}
          >
            {t('Accept conversation', 'Terima percakapan')}
          </Button>
        </div>
      ) : null}
      {q.detail?.status === 'closed' ? (
        <p>
          {t(
            'This case is closed. Its published messages remain available.',
            'Kasus ini sudah ditutup. Pesan yang dipublikasikan tetap dapat dibaca.',
          )}
        </p>
      ) : null}
      {state.available &&
      !canReply &&
      !busy &&
      !state.pending &&
      q.detail?.status !== 'closed' &&
      state.chat?.mode !== 'waiting_staff' ? (
        <p>
          {t(
            'Replies require assignment to you. Email also requires an active verified contact on the case and available email configuration.',
            'Balasan memerlukan penugasan kepada Anda. Email juga memerlukan kontak terverifikasi aktif pada kasus dan konfigurasi email yang tersedia.',
          )}
        </p>
      ) : null}
      {state.available ? (
        <fieldset
          className={styles.compose}
          disabled={locked || !!state.pending}
        >
          <legend>{t('Reply to customer', 'Balas pelanggan')}</legend>
          <div className={styles.form}>
            <label htmlFor={id + '-channel'}>
              {t('Reply channel', 'Kanal balasan')}
            </label>
            <select
              id={id + '-channel'}
              value={state.channel}
              disabled={!!draft}
              onChange={(e) =>
                store.configure(
                  state.language,
                  e.target.value as 'chat' | 'email',
                )
              }
            >
              <option
                value="email"
                disabled={!state.capability?.email_available}
              >
                Email
              </option>
              <option
                value="chat"
                disabled={!state.chat || state.chat.mode !== 'human'}
              >
                Chat
              </option>
            </select>
            {state.channel === 'email' ? (
              <p className={styles.help}>
                {t(
                  'Email goes to the verified contact saved on this case. The customer can reply directly by email.',
                  'Email ditujukan ke kontak terverifikasi yang tersimpan pada kasus. Pelanggan dapat membalas langsung melalui email.',
                )}
              </p>
            ) : null}
            <label htmlFor={id + '-language'}>
              {t('Original reply language', 'Bahasa teks asli')}
            </label>
            <select
              id={id + '-language'}
              value={state.language}
              disabled={!!draft || !canReply}
              onChange={(e) =>
                store.configure(e.target.value as 'en' | 'id', state.channel)
              }
            >
              <option value="en">English</option>
              <option value="id">Bahasa Indonesia</option>
            </select>
            <label htmlFor={id + '-body'}>
              {t('Original reply', 'Balasan asli')}
            </label>
            <textarea
              id={id + '-body'}
              rows={5}
              disabled={!canReply}
              maxLength={8000}
              value={state.body}
              readOnly={!!draft}
              aria-describedby={id + '-length'}
              aria-invalid={[...state.body].length > 4000}
              onChange={(e) => store.edit(e.target.value)}
            />
            <p
              id={id + '-length'}
              className={styles.help}
              role={[...state.body].length > 4000 ? 'alert' : undefined}
            >
              {[...state.body].length}/4000
            </p>
            {!draft ? (
              <div className={styles.row}>
                <Button
                  disabled={
                    !canReply ||
                    !state.body.trim() ||
                    [...state.body].length > 4000
                  }
                  onClick={() => void store.send()}
                >
                  {state.channel === 'email'
                    ? t('Send email reply', 'Kirim balasan email')
                    : t('Send chat reply', 'Kirim balasan chat')}
                </Button>
                <Button
                  variant="secondary"
                  disabled={
                    !canReply ||
                    !state.body.trim() ||
                    [...state.body].length > 4000
                  }
                  onClick={() =>
                    void store.translate(state.language === 'en' ? 'id' : 'en')
                  }
                >
                  {state.language === 'en'
                    ? t(
                        'Preview Indonesian translation',
                        'Pratinjau terjemahan Indonesia',
                      )
                    : t(
                        'Preview English translation',
                        'Pratinjau terjemahan Inggris',
                      )}
                </Button>
              </div>
            ) : null}
            {draft ? (
              <>
                <h4>{t('Translation review', 'Tinjauan terjemahan')}</h4>
                <p className={styles.help}>
                  {t(
                    'No translated reply is sent until you approve it. Check meaning, names and reference numbers.',
                    'Terjemahan tidak dikirim sebelum Anda menyetujuinya. Periksa makna, nama dan nomor referensi.',
                  )}
                </p>
                {draft.state === 'ready' ? (
                  <>
                    <label htmlFor={id + '-review'}>
                      {t('Reviewed translation', 'Terjemahan yang ditinjau')}
                    </label>
                    <textarea
                      id={id + '-review'}
                      rows={5}
                      maxLength={8000}
                      value={state.reviewed}
                      aria-invalid={[...state.reviewed].length > 4000}
                      onChange={(e) => store.review(e.target.value)}
                    />
                    <p className={styles.help}>
                      {t('Draft expires', 'Draft kedaluwarsa')}:{' '}
                      {date(draft.expires_at)}
                    </p>
                    <Button
                      disabled={
                        !store.canReply(draft.channel) ||
                        !state.reviewed.trim() ||
                        [...state.reviewed].length > 4000
                      }
                      onClick={() => void store.approve()}
                    >
                      {t(
                        'Approve and send translation',
                        'Setujui dan kirim terjemahan',
                      )}
                    </Button>
                  </>
                ) : (
                  <p role="status">
                    {draft.state === 'failed'
                      ? t(
                          'A translation could not be prepared. Discard this draft to write a reply manually.',
                          'Terjemahan tidak dapat disiapkan. Buang draft ini untuk menulis balasan secara manual.',
                        )
                      : t(
                          'Translation is still being prepared. Check the saved result.',
                          'Terjemahan masih disiapkan. Periksa hasil tersimpan.',
                        )}
                  </p>
                )}
              </>
            ) : null}
            <Button
              variant="quiet"
              disabled={!dirty || !!state.pending}
              onClick={store.discard}
            >
              {t('Discard reply draft', 'Buang draft balasan')}
            </Button>
          </div>
        </fieldset>
      ) : null}
    </section>
  );
}
