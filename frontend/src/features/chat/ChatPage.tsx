import { Rating } from '../feedback/Rating';
import { useVerification } from '../verification/context';
import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from 'react';
import { useCaseDraft } from '../cases/context';
import { Link, useNavigate, useParams } from 'react-router';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { useChatStore } from './context';
import type { ChatStore, ChatState } from './store';
import { messageSchema } from './contracts';
import { MessageCitations } from './MessageCitations';
import { AgentAnswer } from './AgentAnswer';
import type { MessageInput } from './contracts';
import { useAuthState } from '../../app/auth/context';
import { useRouting } from '../../app/routing/context';
import {
  localizedTarget,
  topics,
  readTopic,
  readHandoffCase,
} from '../../app/routing/policy';
import { useUnsavedChanges } from '../../app/routing/draftState';
import { PageFrame, NotFoundPage } from '../../app/routing/pages';
import { useLocation } from 'react-router';
import { homeCopy } from '../home/content';
import { Button } from '../../components/Button';
import { errorMessage } from '../../api/errors';
import styles from './chat.module.css';
export function ChatPage() {
  const store = useChatStore();
  return store ? (
    <Conversation store={store} />
  ) : (
    <PageFrame>
      <h1>Customer support</h1>
      <p>Session verification is unavailable.</p>
    </PageFrame>
  );
}
function Conversation({ store }: { store: ChatStore }) {
  const rawState = useSyncExternalStore(store.subscribe, store.getState),
    auth = useAuthState(store.auth);
  const { locale } = useRouting(),
    location = useLocation(),
    navigate = useNavigate(),
    { conversationId } = useParams();
  const history = useSyncExternalStore(
    store.timeline.subscribe,
    store.timeline.getState,
  );
  const state =
    conversationId &&
    rawState.meta?.conversation_id !== conversationId &&
    !(
      rawState.phase === 'unavailable' &&
      store.requestedConversationId === conversationId
    )
      ? {
          ...rawState,
          phase: 'loading' as const,
          error: null,
          unresolved: false,
          reviewNeeded: false,
          meta: null,
          messages: [],
          input: { body: '', service: '' as const },
        }
      : rawState;
  const historyReady =
    history.id === conversationId && history.phase === 'ready';
  const threadRef = useRef<HTMLDivElement>(null),
    followLatest = useRef(true);
  useLayoutEffect(() => {
    if (historyReady && threadRef.current && followLatest.current)
      threadRef.current.scrollTop = threadRef.current.scrollHeight;
  }, [historyReady, history.items.length, state.messages.length]);
  const historyReadable =
    historyReady ||
    (history.id === conversationId &&
      state.phase === 'busy' &&
      history.phase === 'paused' &&
      store.historyIsVisible);
  const linkedRatingCase = [...history.items]
    .reverse()
    .find((e) => 'case_id' in e.payload);
  const ratingCaseId =
    linkedRatingCase && 'case_id' in linkedRatingCase.payload
      ? linkedRatingCase.payload.case_id
      : null;
  const kind = auth.phase === 'ready' ? auth.kind : null;
  const verification = useVerification();
  const caseDraft = useCaseDraft();
  const [preparingRequest, setPreparingRequest] = useState(false);
  const [requestError, setRequestError] = useState<string | null>(null);
  const topic = readTopic(location.search);
  const handoffCaseId = readHandoffCase(location.search);
  const t = (en: string, id: string) => (locale === 'en' ? en : id);
  useEffect(() => {
    if (!conversationId) {
      store.newEntry();
      return;
    }
    if (auth.phase === 'ready' && kind !== 'staff')
      void store.open(conversationId).catch(() => {});
  }, [store, conversationId, auth.epoch, auth.phase, kind]);
  useEffect(() => {
    if (
      !conversationId ||
      auth.phase !== 'ready' ||
      kind === 'staff' ||
      rawState.meta?.conversation_id !== conversationId
    )
      return;
    const visible = () =>
      document.visibilityState !== 'hidden' && navigator.onLine;
    const off = store.observeHistory(conversationId, visible());
    const wake = () => {
      if (visible()) store.resumeHistory();
      else store.pauseHistory();
    };
    document.addEventListener('visibilitychange', wake);
    window.addEventListener('online', wake);
    window.addEventListener('offline', wake);
    return () => {
      document.removeEventListener('visibilitychange', wake);
      window.removeEventListener('online', wake);
      window.removeEventListener('offline', wake);
      off();
    };
  }, [
    store,
    conversationId,
    auth.phase,
    auth.epoch,
    kind,
    rawState.meta?.conversation_id,
  ]);
  useUnsavedChanges(
    state.input.body.length > 0 ||
      state.messages.some((m) => m.state !== 'received') ||
      state.phase === 'unknown-create',
    () => store.discard(),
  );
  if (!topic || handoffCaseId === undefined) return <NotFoundPage />;
  const allowed =
    auth.phase === 'ready' &&
    (auth.kind === 'anonymous' || auth.kind === 'customer');
  const target = (key: string) =>
    localizedTarget(
      `${conversationId ? '/help/conversations/' + conversationId : '/help/new'}?topic=${key}${key === 'contact' && handoffCaseId ? '&case=' + handoffCaseId : ''}`,
      locale,
    );
  const titles = homeCopy[locale].topics;
  const pending = state.messages.find((m) => m.state !== 'received');
  const readyForReview =
    state.phase === 'ready' &&
    historyReady &&
    !history.hasMore &&
    history.snapshot?.mode === 'ai' &&
    store.receivedMessages().length > 0 &&
    !pending &&
    !state.unresolved &&
    !state.input.body.trim();
  const run = (fn: () => Promise<unknown>) => {
    void fn().catch(() => {});
  };
  return (
    <PageFrame>
      <div className={styles.layout}>
        <aside className={styles.sidebar}>
          <h2>{t('Help for your journey', 'Bantuan perjalanan Anda')}</h2>
          <p>
            {t(
              'Start with what you need. Find your next step.',
              'Mulai dari kebutuhan Anda. Temukan langkah berikutnya.',
            )}
          </p>
          <nav aria-label={t('Choose a topic', 'Pilih topik')}>
            {topics.map((key) => (
              <Link
                key={key}
                to={
                  key === 'contact'
                    ? target(key)
                    : localizedTarget('/help/new?topic=' + key, locale)
                }
                aria-current={topic === key ? 'page' : undefined}
              >
                {titles[key][0]}
              </Link>
            ))}
          </nav>
          {allowed && kind === 'customer' ? (
            <p>
              <Link to={localizedTarget('/help/services', locale)}>
                {t(
                  'Check booking or payment status',
                  'Periksa status booking atau pembayaran',
                )}
              </Link>
            </p>
          ) : null}
          <Link to={localizedTarget('/', locale)}>
            {t('Back to help centre', 'Kembali ke pusat bantuan')}
          </Link>
        </aside>
        <section className={styles.panel} aria-labelledby="conversation-title">
          <header className={styles.heading}>
            <div>
              <h1 id="conversation-title">{titles[topic][0]}</h1>
              <p>
                {t(
                  'Tell us about an existing service or issue.',
                  'Ceritakan layanan atau kendala yang sudah ada.',
                )}
              </p>
            </div>
            <Link className={styles.support} to={target('contact')}>
              {t('Support', 'Petugas')}
            </Link>
          </header>
          <nav
            className={styles.mobileTopics}
            aria-label={t('Help topics', 'Topik bantuan')}
          >
            {topics.map((key) => (
              <Link
                key={key}
                to={
                  key === 'contact'
                    ? target(key)
                    : localizedTarget('/help/new?topic=' + key, locale)
                }
                aria-current={key === topic ? 'page' : undefined}
              >
                {titles[key][2]}
              </Link>
            ))}
            {allowed && kind === 'customer' && (
              <Link to={localizedTarget('/help/services', locale)}>
                {t('Booking / payment status', 'Status booking / pembayaran')}
              </Link>
            )}
          </nav>
          <div
            ref={threadRef}
            onScroll={() => {
              const node = threadRef.current;
              if (node)
                followLatest.current =
                  node.scrollHeight - node.scrollTop - node.clientHeight < 80;
            }}
            className={`${styles.thread} ${state.phase === 'ready' && !state.messages.length && !history.items.length ? styles.emptyThread : ''}`}
            role="region"
            aria-label={t('Conversation', 'Percakapan')}
            tabIndex={0}
          >
            <div className={styles.intro}>
              <strong>
                {t(
                  history.snapshot && history.snapshot.mode !== 'ai'
                    ? 'Your saved conversation'
                    : 'Tell us what you need help with',
                  history.snapshot && history.snapshot.mode !== 'ai'
                    ? 'Percakapan tersimpan Anda'
                    : 'Ceritakan bantuan yang Anda butuhkan',
                )}
              </strong>
              <p>
                {t(
                  history.snapshot && history.snapshot.mode !== 'ai'
                    ? 'Read your messages and support updates below.'
                    : 'Write your message below and save it. You can then review a support request before sending it to staff.',
                  history.snapshot && history.snapshot.mode !== 'ai'
                    ? 'Baca pesan dan pembaruan bantuan Anda di bawah.'
                    : 'Tulis pesan di bawah lalu simpan. Setelah itu, tinjau permintaan bantuan sebelum mengirimkannya ke petugas.',
                )}
              </p>
              <p>
                {t(
                  'Saved means the message is stored. Replies appear with their original author and language.',
                  'Tersimpan berarti pesan sudah dicatat. Balasan ditampilkan dengan penulis dan bahasa aslinya.',
                )}
              </p>
            </div>
            {topic === 'contact' && (
              <div className={styles.notice}>
                {handoffCaseId && (
                  <p>
                    {t(
                      'Selected support case: ',
                      'Kasus bantuan yang dipilih: ',
                    )}
                    {'CASE-' + handoffCaseId.replaceAll('-', '')}.{' '}
                    {t(
                      'We will check your access before preparing the linked request.',
                      'Kami memeriksa akses Anda sebelum menyiapkan permintaan terkait.',
                    )}
                  </p>
                )}
                <strong>
                  {t('Your story comes with you.', 'Cerita Anda tetap dibawa.')}
                </strong>
                <p>
                  {t(
                    'Keep your issue here for verification and request review. Staff follow-up is by email; opening this page does not send a request.',
                    'Simpan cerita Anda untuk verifikasi dan peninjauan permintaan. Tindak lanjut petugas melalui email; membuka halaman ini tidak mengirim permintaan.',
                  )}
                </p>
              </div>
            )}
            {topic === 'contact' &&
              allowed &&
              kind === 'anonymous' &&
              state.phase === 'ready' &&
              historyReady &&
              store.timeline.canSend() &&
              (state.input.body.trim() ||
                store.receivedMessages().length > 0) && (
                <Link
                  className={styles.support}
                  to={localizedTarget(
                    '/help/verify' +
                      (handoffCaseId ? '?case=' + handoffCaseId : ''),
                    locale,
                  )}
                  onClick={(event) => {
                    try {
                      verification?.prepareFromChat();
                    } catch {
                      event.preventDefault();
                      setRequestError(
                        t(
                          'The conversation is updating. Check the latest messages and try email verification again.',
                          'Percakapan sedang diperbarui. Periksa pesan terbaru dan coba verifikasi email kembali.',
                        ),
                      );
                    }
                  }}
                >
                  {t(
                    'Continue to email verification',
                    'Lanjut ke verifikasi email',
                  )}
                </Link>
              )}
            {!!conversationId && (
              <p className={styles.note}>
                {t(
                  'Saved conversation history is loaded from the service. Your unsent text stays separate.',
                  'Riwayat percakapan tersimpan dimuat dari layanan. Teks yang belum dikirim tetap terpisah.',
                )}
              </p>
            )}
            {conversationId && (
              <div className={styles.historyState}>
                {historyReady && history.snapshot ? (
                  <p role="status">
                    {
                      {
                        ai: t('Assistant conversation', 'Percakapan asisten'),
                        waiting_staff: t(
                          'Waiting for a staff member',
                          'Menunggu petugas',
                        ),
                        human: t(
                          'A staff member is handling this conversation',
                          'Percakapan sedang ditangani petugas',
                        ),
                        ended: t(
                          'This conversation has ended',
                          'Percakapan ini sudah berakhir',
                        ),
                      }[history.snapshot.mode]
                    }
                  </p>
                ) : (
                  <p role="status">
                    {history.phase === 'error'
                      ? t(
                          'Conversation history is unavailable.',
                          'Riwayat percakapan belum tersedia.',
                        )
                      : history.phase === 'paused'
                        ? t(
                            state.phase === 'busy'
                              ? 'Saving your change…'
                              : 'Updates paused while this page is hidden or offline.',
                            state.phase === 'busy'
                              ? 'Menyimpan perubahan Anda…'
                              : 'Pembaruan dijeda saat halaman tersembunyi atau offline.',
                          )
                        : t(
                            'Loading conversation history…',
                            'Memuat riwayat percakapan…',
                          )}
                  </p>
                )}
                {history.hasMore && (
                  <p role="status">
                    {t(
                      'Loading more saved history…',
                      'Memuat riwayat tersimpan lainnya…',
                    )}
                  </p>
                )}
                {history.phase === 'error' && (
                  <Button
                    variant="secondary"
                    onClick={() => {
                      store.timeline.reload();
                    }}
                  >
                    {t(
                      'Reload conversation history',
                      'Muat ulang riwayat percakapan',
                    )}
                  </Button>
                )}
              </div>
            )}
            <div
              role="log"
              aria-live="polite"
              aria-relevant="additions text"
              className={styles.messages}
            >
              {historyReadable &&
                (state.phase === 'ready' || state.phase === 'busy') &&
                history.items.map((event) =>
                  'message_id' in event.payload ? (
                    <article
                      key={event.id}
                      className={`${styles.message} ${event.payload.author_type === 'customer' ? '' : styles.reply}`}
                    >
                      <p className={styles.author}>
                        {event.payload.author_type === 'customer'
                          ? t('You', 'Anda')
                          : event.payload.author_type === 'staff'
                            ? t('Support staff', 'Petugas')
                            : t('AI assistant', 'Asisten AI')}{' '}
                        ·{' '}
                        {event.payload.language === 'en'
                          ? 'English'
                          : 'Bahasa Indonesia'}
                      </p>
                      <p className={styles.body}>{event.payload.body}</p>
                      <p className={styles.messageStatus}>
                        {t('Saved', 'Tersimpan')}
                      </p>
                      {allowed &&
                        conversationId &&
                        event.event_type === 'ai_message' &&
                        history.snapshot && (
                          <MessageCitations
                            key={location.key}
                            auth={store.auth}
                            conversationId={conversationId}
                            messageId={event.payload.message_id}
                            generationEpoch={history.snapshot.generation_epoch}
                            locale={locale}
                          />
                        )}
                    </article>
                  ) : (
                    <p key={event.id} className={styles.event}>
                      {event.event_type === 'language_changed'
                        ? t(
                            'Conversation language changed: ',
                            'Bahasa percakapan diubah: ',
                          ) + event.payload.language
                        : {
                            handoff_requested: t(
                              'Support requested',
                              'Bantuan petugas diminta',
                            ),
                            chat_accepted: t(
                              'A staff member joined',
                              'Petugas bergabung',
                            ),
                            chat_reassigned: t(
                              'Case responsibility updated',
                              'Penanggung jawab diperbarui',
                            ),
                            chat_ended: t(
                              'Conversation ended',
                              'Percakapan berakhir',
                            ),
                            chat_updated: t(
                              'Case handling updated',
                              'Penanganan kasus diperbarui',
                            ),
                            message_received: '',
                            staff_message: '',
                            ai_message: '',
                          }[event.event_type]}
                    </p>
                  ),
                )}
              {((historyReadable ||
                (state.phase === 'busy' && store.historyIsVisible)) &&
              (state.phase === 'ready' || state.phase === 'busy')
                ? state.messages.filter(
                    (m) =>
                      !m.receipt ||
                      !historyReadable ||
                      !history.items.some(
                        (e) =>
                          'message_id' in e.payload &&
                          e.payload.message_id === m.receipt!.message_id,
                      ),
                  )
                : []
              ).map((message) => (
                <article key={message.clientId} className={styles.message}>
                  <p className={styles.author}>
                    {t('You', 'Anda')} ·{' '}
                    {message.language === 'en' ? 'English' : 'Bahasa Indonesia'}
                  </p>
                  <p className={styles.body}>{message.body}</p>
                  <p className={styles.messageStatus}>
                    {message.state === 'received'
                      ? t('Saved', 'Tersimpan')
                      : message.state === 'sending'
                        ? t('Saving…', 'Menyimpan…')
                        : message.state === 'unknown'
                          ? t('Not confirmed', 'Belum dipastikan')
                          : t('Not saved', 'Belum tersimpan')}
                  </p>
                </article>
              ))}
            </div>
            {allowed &&
              conversationId &&
              state.meta &&
              (state.phase === 'ready' || state.phase === 'busy') && (
                <AgentAnswer
                  key={auth.epoch + ':' + conversationId + ':' + location.key}
                  store={store}
                  conversationId={conversationId}
                  routeKey={location.key}
                  locale={locale}
                />
              )}
            {allowed &&
              readyForReview &&
              caseDraft &&
              (kind === 'customer' ||
                topic === 'information' ||
                topic === 'contact') && (
                <section
                  className={styles.nextStep}
                  aria-label={t('Staff assistance', 'Bantuan petugas')}
                >
                  <div>
                    <strong>
                      {t('Talk to the support team', 'Hubungi tim bantuan')}
                    </strong>
                    <p>
                      {handoffCaseId && kind === 'anonymous'
                        ? t(
                            'Verify your identity before linking this conversation to your existing case.',
                            'Verifikasi identitas sebelum menghubungkan percakapan ke kasus Anda.',
                          )
                        : kind === 'anonymous'
                          ? t(
                              'General assistance in this browser does not require email. Verify your identity for booking or payment requests and email follow-up.',
                              'Bantuan umum di browser ini tidak memerlukan email. Verifikasi identitas untuk permintaan booking atau pembayaran serta tindak lanjut email.',
                            )
                          : t(
                              'Keep your saved story. Review the request and confirm before asking staff to join this conversation.',
                              'Cerita tersimpan tetap dibawa. Tinjau permintaan dan konfirmasi sebelum meminta petugas bergabung dalam percakapan ini.',
                            )}
                    </p>
                  </div>
                  <Button
                    disabled={
                      preparingRequest ||
                      (!!handoffCaseId && kind !== 'customer')
                    }
                    onClick={() => {
                      setPreparingRequest(true);
                      setRequestError(null);
                      const verified = verification?.getState();
                      void caseDraft
                        .prepareFromChat(
                          kind === 'anonymous' ? 'information' : topic,
                          verified?.continuedId === conversationId
                            ? verified.contact
                            : '',
                          'request_handoff',
                          handoffCaseId,
                        )
                        .then(async () => {
                          const prepared = caseDraft.getState();
                          if (prepared.knownDraftId && !prepared.uncertain)
                            await navigate(
                              localizedTarget(
                                '/help/submissions/' + prepared.knownDraftId,
                                locale,
                              ),
                            );
                          else if (prepared.phase === 'intake')
                            await navigate(
                              localizedTarget('/help/request', locale),
                            );
                          else throw Error('Preparation unavailable');
                        })
                        .catch(() =>
                          setRequestError(
                            t(
                              'The request could not be prepared. Check any existing preview before preparing another.',
                              'Permintaan belum dapat disiapkan. Periksa preview yang sudah ada sebelum menyiapkan yang lain.',
                            ),
                          ),
                        )
                        .finally(() => setPreparingRequest(false));
                    }}
                  >
                    {t('Review staff request', 'Tinjau permintaan petugas')}
                  </Button>
                </section>
              )}
            {allowed && kind === 'customer' && readyForReview && caseDraft && (
              <section
                className={styles.nextStep}
                aria-label={t('Next step', 'Langkah berikutnya')}
              >
                <div>
                  <strong>
                    {t('Your message is saved', 'Pesan Anda sudah tersimpan')}
                  </strong>
                  <p>
                    {t(
                      'Review the details next. You will confirm before anything is sent to support.',
                      'Selanjutnya, tinjau detailnya. Anda akan mengonfirmasi sebelum permintaan dikirim ke petugas.',
                    )}
                  </p>
                </div>
                <Button
                  disabled={preparingRequest}
                  onClick={() => {
                    setPreparingRequest(true);
                    setRequestError(null);
                    const verified = verification?.getState();
                    void caseDraft
                      .prepareFromChat(
                        topic,
                        verified && verified.continuedId === conversationId
                          ? verified.contact
                          : '',
                      )
                      .then(async () => {
                        if (caseDraft.getState().phase === 'intake')
                          await navigate(
                            localizedTarget('/help/request', locale),
                          );
                        else {
                          const failure = caseDraft.getState().error;
                          setRequestError(
                            failure
                              ? errorMessage(failure, locale)
                              : t(
                                  'The request could not be prepared. Check your session before trying again.',
                                  'Permintaan belum dapat disiapkan. Periksa sesi sebelum mencoba lagi.',
                                ),
                          );
                        }
                      })
                      .catch(() =>
                        setRequestError(
                          t(
                            'The request could not be prepared. Check your session before trying again.',
                            'Permintaan belum dapat disiapkan. Periksa sesi sebelum mencoba lagi.',
                          ),
                        ),
                      )
                      .finally(() => setPreparingRequest(false));
                  }}
                >
                  {preparingRequest
                    ? t('Preparing review…', 'Menyiapkan tinjauan…')
                    : t('Review support request', 'Tinjau permintaan bantuan')}
                </Button>
              </section>
            )}
            {requestError && (
              <div role="alert" className={styles.notice}>
                {requestError}
                {caseDraft?.getState().knownDraftId && (
                  <Link
                    to={localizedTarget(
                      '/help/submissions/' + caseDraft.getState().knownDraftId,
                      locale,
                    )}
                  >
                    {t(
                      'Open your existing preview',
                      'Buka preview yang sudah ada',
                    )}
                  </Link>
                )}
              </div>
            )}
            {state.reviewNeeded && (
              <p className={styles.notice} role="status">
                {t(
                  'The previous attempt was rejected. Your access has been checked again. Review your text before sending a new message.',
                  'Upaya sebelumnya ditolak. Akses Anda sudah diperiksa ulang. Tinjau teks Anda sebelum mengirim pesan baru.',
                )}
              </p>
            )}
            {state.unresolved && (
              <p className={styles.notice} role="alert">
                {t(
                  'A previous message has no confirmed result. Its old send command was cleared during a session check. Do not resend the text as a new message.',
                  'Hasil pesan sebelumnya belum dipastikan. Perintah kirim lama dibuang saat pemeriksaan sesi. Jangan kirim ulang teks tersebut sebagai pesan baru.',
                )}
              </p>
            )}
            {state.phase === 'unknown-create' && (
              <p className={styles.notice} role="alert">
                {t(
                  'The conversation may have been created. Do not start another automatically; its reference could not be recovered.',
                  'Percakapan mungkin sudah dibuat. Jangan otomatis membuat yang baru; referensinya belum dapat dipulihkan.',
                )}
              </p>
            )}
            {state.error && (
              <div className={styles.notice} role="alert">
                <p>{errorMessage(state.error, locale)}</p>
                {pending &&
                  state.phase === 'ready' &&
                  state.error.code !== 'MESSAGE_CONFLICT' && (
                    <Button
                      variant="secondary"
                      onClick={() => run(() => store.send(true))}
                    >
                      {t(
                        'Retry this same message',
                        'Coba ulang pesan yang sama',
                      )}
                    </Button>
                  )}
                {state.phase === 'unavailable' && conversationId && (
                  <Button
                    variant="secondary"
                    onClick={() => run(() => store.open(conversationId))}
                  >
                    {t('Check conversation', 'Periksa percakapan')}
                  </Button>
                )}
              </div>
            )}
            {(auth.phase === 'checking' ||
              auth.phase === 'transition' ||
              state.phase === 'loading') && (
              <p role="status">
                {t(
                  'Checking your session and conversation…',
                  'Memeriksa sesi dan percakapan…',
                )}
              </p>
            )}
            {!conversationId && state.phase !== 'unknown-create' && (
              <div className={styles.start}>
                <p>
                  {t(
                    'Start explicitly to create a conversation. Opening this screen does not create one.',
                    'Pilih mulai untuk membuat percakapan. Membuka layar ini tidak membuat percakapan.',
                  )}
                </p>
                <Button
                  disabled={
                    state.phase === 'busy' ||
                    (auth.phase !== 'guest' && !allowed)
                  }
                  onClick={() =>
                    run(async () => {
                      const id = await store.create(locale);
                      if (id)
                        await navigate(
                          localizedTarget(
                            '/help/conversations/' +
                              id +
                              '?topic=' +
                              topic +
                              (handoffCaseId ? '&case=' + handoffCaseId : ''),
                            locale,
                          ),
                          { replace: true },
                        );
                    })
                  }
                >
                  {state.phase === 'busy'
                    ? t('Starting…', 'Memulai…')
                    : t('Start conversation', 'Mulai percakapan')}
                </Button>
              </div>
            )}
            {auth.phase === 'unavailable' && (
              <div className={styles.notice}>
                <p>
                  {t(
                    'A secure session is unavailable. Use the integrated HTTPS site and check your session.',
                    'Sesi aman belum tersedia. Gunakan situs HTTPS terintegrasi dan periksa sesi Anda.',
                  )}
                </p>
                <Button
                  variant="secondary"
                  onClick={() => run(() => store.auth.recover())}
                >
                  {t('Check session', 'Periksa sesi')}
                </Button>
              </div>
            )}
            {auth.phase === 'ready' && auth.kind === 'staff' && (
              <p role="alert">
                {t(
                  'This is a staff session. Customer messages require a customer or anonymous session.',
                  'Ini sesi petugas. Pesan pelanggan memerlukan sesi pelanggan atau anonim.',
                )}
              </p>
            )}
          </div>
          {allowed &&
            (historyReadable ||
              (state.phase === 'busy' && store.historyIsVisible)) &&
            state.meta &&
            (state.phase === 'ready' || state.phase === 'busy') && (
              <div className={styles.composer}>
                <Composer
                  key={auth.epoch + ':' + state.meta.conversation_id}
                  store={store}
                  state={state}
                  historyBlocked={
                    !historyReady ||
                    history.hasMore ||
                    history.snapshot?.mode === 'ended'
                  }
                  locale={locale}
                  languageControl={
                    <div className={styles.language}>
                      <p>
                        {t('Conversation language', 'Bahasa percakapan')}:{' '}
                        {state.meta.language === 'en'
                          ? 'English'
                          : 'Bahasa Indonesia'}
                      </p>
                      {state.meta.language !== locale && (
                        <Button
                          variant="quiet"
                          disabled={
                            state.phase !== 'ready' ||
                            !!pending ||
                            state.unresolved ||
                            !historyReadable ||
                            history.hasMore ||
                            history.snapshot?.mode === 'ended'
                          }
                          onClick={() => run(() => store.language(locale))}
                        >
                          {t(
                            'Use English for new messages',
                            'Gunakan Bahasa Indonesia untuk pesan baru',
                          )}
                        </Button>
                      )}
                    </div>
                  }
                />
              </div>
            )}
          {allowed &&
            historyReady &&
            state.meta &&
            (state.phase === 'ready' || state.phase === 'busy') && (
              <Rating
                kind={ratingCaseId ? 'case' : 'conversation'}
                id={ratingCaseId ?? state.meta.conversation_id}
                revision={
                  history.items.length + ':' + (history.snapshot?.mode ?? '')
                }
              />
            )}
        </section>
      </div>
    </PageFrame>
  );
}
function Composer({
  store,
  state,
  locale,
  languageControl,
  historyBlocked,
}: {
  store: ChatStore;
  state: ChatState;
  locale: 'en' | 'id';
  languageControl: ReactNode;
  historyBlocked: boolean;
}) {
  const t = (en: string, id: string) => (locale === 'en' ? en : id);
  const {
    register,
    handleSubmit,
    getValues,
    formState: { errors },
  } = useForm<MessageInput>({
    values: state.input,
    resolver: zodResolver(messageSchema),
  });
  const disabled =
    state.phase !== 'ready' ||
    historyBlocked ||
    state.unresolved ||
    state.messages.some((m) => m.state !== 'received') ||
    state.meta?.language !== locale;
  const change = () => {
    try {
      store.input(getValues());
    } catch {
      /* boundary reset hides stale form */
    }
  };
  return (
    <form
      noValidate
      onSubmit={handleSubmit(() => store.send().catch(() => {}))}
    >
      <div className={styles.composerTools}>
        {languageControl}
        <details className={styles.service}>
          <summary>
            {t('Choose a service (optional)', 'Pilih layanan (opsional)')}
          </summary>
          <label htmlFor="service">
            {t('Service (optional)', 'Layanan (opsional)')}
          </label>
          <select
            id="service"
            disabled={disabled}
            {...register('service', { onChange: change })}
          >
            <option value="">{t('Not sure yet', 'Belum tahu')}</option>
            <option value="parking">{t('Parking', 'Parkir')}</option>
            <option value="lounge">Lounge</option>
            <option value="other">{t('Other service', 'Layanan lain')}</option>
          </select>
        </details>
      </div>
      <label htmlFor="message">{t('Your message', 'Pesan Anda')}</label>
      <textarea
        id="message"
        rows={2}
        maxLength={3800}
        disabled={disabled}
        aria-invalid={!!errors.body}
        aria-describedby="message-help message-error"
        {...register('body', { onChange: change })}
      />
      <p id="message-help" className={styles.help}>
        {t(
          'Up to 3,800 characters. Do not include passwords, card numbers or verification codes.',
          'Maksimal 3.800 karakter. Jangan sertakan kata sandi, nomor kartu, atau kode verifikasi.',
        )}
      </p>
      <p
        id="message-error"
        className={styles.validation}
        role={errors.body ? 'alert' : undefined}
      >
        {errors.body
          ? t(
              'Enter a message without null characters, up to 3,800 characters.',
              'Isi pesan tanpa karakter null, maksimal 3.800 karakter.',
            )
          : ''}
      </p>
      <Button
        type="submit"
        disabled={disabled || !state.input.body.trim()}
        variant={
          store.receivedMessages().length > 0 && !state.input.body.trim()
            ? 'secondary'
            : 'primary'
        }
      >
        {state.phase === 'busy'
          ? t('Saving…', 'Menyimpan…')
          : t('Save message', 'Simpan pesan')}
      </Button>
    </form>
  );
}
