import {
  useEffect,
  useLayoutEffect,
  useState,
  useSyncExternalStore,
} from 'react';
import { useRouting } from '../../app/routing/context';
import { useUnsavedChanges } from '../../app/routing/draftState';
import { Button } from '../../components/Button';
import { errorMessage } from '../../api/errors';
import { informationMessage } from './customer-contracts';
import { useCustomerInformation } from './information-store';
import type { CustomerInformationStore } from './information-store';
import type { CaseDetailStore } from './detail-store';
import type { CaseDetail } from './detail-contracts';
import styles from './cases.module.css';

export function AdditionalInformation({
  detail,
  details,
}: {
  detail: CaseDetail;
  details: CaseDetailStore;
}) {
  const store = useCustomerInformation();
  return store ? (
    <InformationForm store={store} detail={detail} details={details} />
  ) : null;
}
function InformationForm({
  store,
  detail,
  details,
}: {
  store: CustomerInformationStore;
  detail: CaseDetail;
  details: CaseDetailStore;
}) {
  const state = useSyncExternalStore(store.subscribe, store.getState),
    { locale } = useRouting();
  const t = (en: string, id: string) => (locale === 'en' ? en : id);
  useLayoutEffect(() => {
    store.open(detail.id, detail.version, detail.status === 'closed');
  }, [store, detail.id, detail.version, detail.status]);
  useEffect(() => {
    if (
      state.phase === 'saved' &&
      state.receipt &&
      state.receipt.case_version > detail.version
    )
      void details.open(detail.id);
  }, [state.phase, state.receipt, details, detail.id, detail.version]);
  useUnsavedChanges(
    !!state.message ||
      ['review', 'reviewing', 'sending', 'unknown', 'recovering'].includes(
        state.phase,
      ),
    () => store.discard(),
  );
  const busy = ['reviewing', 'sending', 'recovering'].includes(state.phase);
  const uncertain = state.phase === 'unknown' || state.phase === 'recovering';
  const count = Array.from(state.message).length;
  if (store.caseId !== detail.id) return null;
  return (
    <section
      className={styles.section}
      aria-labelledby="additional-information-title"
    >
      <h2 id="additional-information-title">
        {t('Add information', 'Tambahkan informasi')}
      </h2>
      <p>
        {t(
          'Send relevant details for this case. Your message is shared as written and does not change the case status.',
          'Kirim rincian terkait kasus ini. Pesan dibagikan sesuai tulisan Anda dan tidak mengubah status kasus.',
        )}
      </p>
      {state.error && (
        <p role="alert" className={styles.error}>
          {errorMessage(state.error, locale)}
        </p>
      )}
      {state.phase === 'saved' && (
        <p role="status" className={styles.notice}>
          {t(
            'Your additional information was recorded.',
            'Informasi tambahan Anda tercatat.',
          )}
        </p>
      )}
      {state.phase === 'sending' && (
        <p role="status">
          {t(
            'Sending your information. The result is not confirmed yet.',
            'Mengirim informasi Anda. Hasilnya belum dipastikan.',
          )}
        </p>
      )}
      {uncertain ? (
        <div className={styles.notice} role="status">
          <p>
            {t(
              'We cannot confirm whether your message was saved. Check its receipt before sending anything else. A missing receipt does not prove it was not saved.',
              'Kami belum dapat memastikan apakah pesan tersimpan. Periksa bukti penerimaan sebelum mengirim lagi. Bukti yang belum ditemukan tidak membuktikan pesan gagal tersimpan.',
            )}
          </p>
          <Button
            variant="secondary"
            loading={busy}
            onClick={() => {
              void store.recover();
            }}
          >
            {t('Check message receipt', 'Periksa bukti penerimaan')}
          </Button>
        </div>
      ) : state.phase === 'review' && state.preview ? (
        <Review
          key={state.preview.preview_id}
          store={store}
          message={state.preview.message}
        />
      ) : detail.status !== 'closed' ? (
        <form
          className={styles.form}
          onSubmit={(e) => {
            e.preventDefault();
            if (informationMessage.safeParse(state.message).success)
              void store.review(locale);
          }}
        >
          <fieldset disabled={busy}>
            <label htmlFor="additional-information-message">
              {t('Your additional information', 'Informasi tambahan Anda')}
            </label>
            <textarea
              id="additional-information-message"
              rows={5}
              value={state.message}
              required
              aria-describedby="additional-information-help"
              onChange={(e) => store.edit(e.target.value)}
            />
            <p
              id="additional-information-help"
              className={count > 4000 ? styles.fieldError : styles.help}
            >
              {count} / 4000 ·{' '}
              {t(
                'Do not include passwords, card details or verification codes.',
                'Jangan sertakan kata sandi, data kartu atau kode verifikasi.',
              )}
            </p>
            <Button
              type="submit"
              loading={busy}
              disabled={!informationMessage.safeParse(state.message).success}
            >
              {t('Review message', 'Tinjau pesan')}
            </Button>
          </fieldset>
        </form>
      ) : (
        <p className={styles.help}>
          {t(
            'This case is closed. Use the same-issue follow-up option above if you still need help.',
            'Kasus ditutup. Gunakan pilihan tindak lanjut masalah yang sama di atas jika masih membutuhkan bantuan.',
          )}
        </p>
      )}
    </section>
  );
}
function Review({
  store,
  message,
}: {
  store: CustomerInformationStore;
  message: string;
}) {
  const [confirmed, setConfirmed] = useState(false),
    { locale } = useRouting();
  const t = (en: string, id: string) => (locale === 'en' ? en : id);
  return (
    <div className={styles.form}>
      <h3>{t('Review before sending', 'Tinjau sebelum mengirim')}</h3>
      <p className={styles.report}>{message}</p>
      <label className={styles.consent}>
        <input
          type="checkbox"
          checked={confirmed}
          onChange={(e) => setConfirmed(e.target.checked)}
        />
        <span>
          {t(
            'I confirm that this information should be added to this case.',
            'Saya mengonfirmasi informasi ini untuk ditambahkan ke kasus ini.',
          )}
        </span>
      </label>
      <div className={styles.actions}>
        <Button
          disabled={!confirmed}
          onClick={() => {
            void store.confirm(confirmed);
          }}
        >
          {t('Confirm and send', 'Konfirmasi dan kirim')}
        </Button>
        <Button variant="secondary" onClick={() => store.edit(message)}>
          {t('Edit message', 'Ubah pesan')}
        </Button>
      </div>
    </div>
  );
}
