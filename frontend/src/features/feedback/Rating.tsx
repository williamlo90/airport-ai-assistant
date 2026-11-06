import {
  useContext,
  useEffect,
  useMemo,
  useSyncExternalStore,
  useState,
  useRef,
  useLayoutEffect,
} from 'react';
import { AuthContext, useAuthState } from '../../app/auth/context';
import { useRouting } from '../../app/routing/context';
import { useUnsavedChanges } from '../../app/routing/draftState';
import { Button } from '../../components/Button';
import { errorMessage } from '../../api/errors';
import { RatingStore } from './store';
import type { Target } from './contracts';
import { ratingInput } from './contracts';
import styles from './feedback.module.css';
export function Rating({
  kind,
  id,
  revision,
}: {
  kind: Target['kind'];
  id: string;
  revision: string | number;
}) {
  const auth = useContext(AuthContext),
    state = useAuthState(auth),
    store = useMemo(() => (auth ? new RatingStore(auth) : null), [auth]);
  const kindReady = state.phase === 'ready' ? state.kind : null;
  useEffect(() => store?.connect(), [store]);
  useLayoutEffect(() => {
    if (store && kindReady && kindReady !== 'staff')
      void store.open({ kind, id });
    return () => store?.clear();
  }, [store, kind, id, revision, state.phase, state.epoch, kindReady]);
  return store && kindReady && kindReady !== 'staff' ? (
    <RatingPanel store={store} />
  ) : null;
}
export function RatingPanel({ store }: { store: RatingStore }) {
  const state = useSyncExternalStore(store.subscribe, store.getState),
    { locale } = useRouting(),
    t = (en: string, id: string) => (locale === 'en' ? en : id);
  if (state.phase === 'idle' || state.phase === 'loading')
    return (
      <p role="status">
        {t('Loading optional feedback…', 'Memuat penilaian opsional…')}
      </p>
    );
  if (state.error?.status === 403 || state.error?.status === 404) return null;
  if (state.error)
    return (
      <section className={styles.panel}>
        <h2>{t('Your feedback', 'Penilaian Anda')}</h2>
        <p role="alert">{errorMessage(state.error, locale)}</p>
        {state.phase === 'unknown' && (
          <p>
            {t(
              'The save result is uncertain. Check the current rating before making another change.',
              'Hasil penyimpanan belum pasti. Periksa penilaian saat ini sebelum mengubahnya lagi.',
            )}
          </p>
        )}
        <Button variant="secondary" onClick={() => void store.recover()}>
          {t('Check current rating', 'Periksa penilaian saat ini')}
        </Button>
      </section>
    );
  if (!state.value || (!state.value.eligible && !state.value.rating))
    return null;
  return (
    <section
      className={styles.panel}
      aria-label={t('Optional feedback', 'Penilaian opsional')}
    >
      <h2>
        {t(
          'How was the help you received?',
          'Bagaimana bantuan yang Anda terima?',
        )}
      </h2>
      <p>
        {t(
          'Rating is optional. It does not close your request or prevent further help.',
          'Penilaian tidak wajib. Penilaian tidak menutup permintaan atau menghalangi bantuan berikutnya.',
        )}
      </p>
      {state.notice && (
        <p role="status">
          {state.notice === 'saved'
            ? t('Your rating was saved.', 'Penilaian Anda tersimpan.')
            : t(
                'This is the current saved rating. It does not confirm which request saved it.',
                'Ini penilaian yang tersimpan saat ini. Ini tidak memastikan permintaan mana yang menyimpannya.',
              )}
        </p>
      )}
      <RatingForm
        key={
          (state.value.rating?.id ?? 'new') +
          ':' +
          (state.value.rating?.version ?? 0)
        }
        store={store}
      />
    </section>
  );
}
function RatingForm({ store }: { store: RatingStore }) {
  const state = useSyncExternalStore(store.subscribe, store.getState),
    value = state.value!,
    { locale } = useRouting(),
    t = (en: string, id: string) => (locale === 'en' ? en : id);
  const [score, setScore] = useState<number | null>(
      value.rating?.score ?? null,
    ),
    [comment, setComment] = useState(value.rating?.comment ?? ''),
    [invalid, setInvalid] = useState(false),
    [skipped, skip] = useState(false);
  const invalidFocus = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (invalid) invalidFocus.current?.focus();
  }, [invalid]);
  const dirty =
    score !== (value.rating?.score ?? null) ||
    comment !== (value.rating?.comment ?? '');
  useUnsavedChanges(dirty || state.phase === 'sending', () => {
    setScore(value.rating?.score ?? null);
    setComment(value.rating?.comment ?? '');
    skip(true);
  });
  const busy = state.phase === 'sending',
    editable = value.eligible && !busy;
  if (skipped)
    return (
      <Button variant="quiet" onClick={() => skip(false)}>
        {t('Give feedback', 'Beri penilaian')}
      </Button>
    );
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        const input = ratingInput.safeParse({
          score,
          comment: comment || null,
          expected_version: value.rating?.version ?? 0,
        });
        setInvalid(!input.success);
        if (input.success) void store.save(input.data.score, comment);
      }}
    >
      <fieldset disabled={!editable}>
        <legend>{t('Rating from 1 to 5', 'Nilai 1 sampai 5')}</legend>
        <div className={styles.scores}>
          {[1, 2, 3, 4, 5].map((n) => (
            <label key={n}>
              <input
                type="radio"
                name="rating-score"
                value={n}
                checked={score === n}
                onChange={() => setScore(n)}
              />
              <span>{n}</span>
            </label>
          ))}
        </div>
        <p>{t('1 = poor · 5 = excellent', '1 = buruk · 5 = sangat baik')}</p>
        <label className={styles.label}>
          {t('Comment (optional)', 'Komentar (opsional)')}
          <textarea
            value={comment}
            maxLength={4000}
            onChange={(e) => setComment(e.target.value)}
            rows={3}
          />
        </label>
        <p>{Array.from(comment).length}/2000</p>
      </fieldset>
      {invalid && (
        <p role="alert" tabIndex={-1} ref={invalidFocus}>
          {t(
            'Choose a rating and use up to 2,000 characters. A comment cannot contain only spaces.',
            'Pilih nilai dan gunakan maksimal 2.000 karakter. Komentar tidak boleh hanya spasi.',
          )}
        </p>
      )}
      {!value.eligible && (
        <p>
          {t(
            'You can read this rating. Changes are available after the case is closed.',
            'Anda dapat membaca penilaian ini. Perubahan tersedia setelah kasus ditutup.',
          )}
        </p>
      )}
      <div className={styles.actions}>
        {value.eligible && (
          <Button type="submit" disabled={busy}>
            {busy
              ? t('Saving…', 'Menyimpan…')
              : t('Save rating', 'Simpan penilaian')}
          </Button>
        )}
        <Button
          variant="quiet"
          disabled={busy}
          onClick={() => {
            setScore(value.rating?.score ?? null);
            setComment(value.rating?.comment ?? '');
            skip(true);
          }}
        >
          {t('Skip for now', 'Lewati dulu')}
        </Button>
      </div>
    </form>
  );
}
