import {
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
import { StaffActionStore } from './action-store';
import type { StaffQueueStore } from './queue-store';
import type { WorkStatus, CloseReason } from './action-contracts';
import { useRouting } from '../../app/routing/context';
import { useUnsavedChanges } from '../../app/routing/draftState';
import { Button } from '../../components/Button';
import { Dialog } from '../../components/Dialog';
import styles from './actions.module.css';
export function StaffActions({
  queue,
  caseId,
  onLockChange,
}: {
  queue: StaffQueueStore;
  caseId: string;
  onLockChange: (locked: boolean) => void;
}) {
  const store = useMemo(
    () => new StaffActionStore(queue, caseId),
    [queue, caseId],
  );
  useEffect(() => {
    const off = store.connect();
    return () => {
      off();
      store.dispose();
    };
  }, [store]);
  const state = useSyncExternalStore(store.subscribe, store.getState),
    q = useSyncExternalStore(queue.subscribe, queue.getState);
  const { locale } = useRouting(),
    t = (en: string, id: string) => (locale === 'en' ? en : id);
  const [now, setNow] = useState(Date.now);
  const feedback = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (state.notice === 'invalid' || state.notice === 'invalid-close')
      feedback.current?.focus();
  }, [state.notice]);
  const dirty = store.dirty();
  useUnsavedChanges(dirty, store.discard);
  useEffect(() => {
    onLockChange(dirty && q.detailPhase === 'ready');
    return () => onLockChange(false);
  }, [dirty, q.detailPhase, onLockChange]);
  useEffect(() => {
    if (state.retryAt <= Date.now()) return;
    const timer = setInterval(() => {
      const current = Date.now();
      setNow(current);
      if (current >= state.retryAt) clearInterval(timer);
    }, 1000);
    return () => clearInterval(timer);
  }, [state.retryAt]);
  const detail =
    q.selectedId === caseId &&
    q.detail?.id === caseId &&
    q.capabilities?.case_id === caseId &&
    q.detailPhase === 'ready'
      ? q.detail
      : null;
  const cap = detail ? q.capabilities : null;
  const busy = state.phase === 'sending' || state.phase === 'checking',
    disabled = busy || state.phase === 'review' || state.retryAt > now;
  const label = (s: string) =>
    ({
      in_progress: t('In progress', 'Ditangani'),
      awaiting_customer: t('Awaiting customer', 'Menunggu pelanggan'),
      awaiting_external: t('Awaiting external', 'Menunggu pihak luar'),
    })[s] ?? s;
  const staffName = (value: {
    display_name: string | null;
    membership_id: string;
  }) =>
    (value.display_name ?? t('Staff member', 'Petugas')) +
    ' (' +
    (state.directory?.items.some(
      (other) =>
        other.membership_id !== value.membership_id &&
        other.membership_id.endsWith(value.membership_id.slice(-8)),
    )
      ? value.membership_id
      : value.membership_id.slice(-8)) +
    ')';
  const closeLabel = (reason: string) =>
    ({
      resolved: t('Resolved', 'Selesai'),
      customer_cancelled: t('Cancelled by customer', 'Dibatalkan pelanggan'),
      duplicate: t('Duplicate case', 'Kasus duplikat'),
      no_response: t('No customer response', 'Tidak ada respons pelanggan'),
    })[reason] ?? reason;
  return (
    <section
      className={styles.actions}
      aria-label={t('Case actions', 'Tindakan kasus')}
    >
      {state.notice ? (
        <p
          id="staff-action-feedback"
          ref={feedback}
          tabIndex={-1}
          role={state.notice === 'saved' ? 'status' : 'alert'}
          className={styles.notice}
        >
          {
            {
              saved: t(
                'Change recorded. Current case details must be available before another action.',
                'Perubahan tercatat. Detail kasus terbaru harus tersedia sebelum tindakan berikutnya.',
              ),
              'recorded-unavailable': t(
                'Change recorded, but the latest case or permissions could not be loaded. Refresh before making another change.',
                'Perubahan tercatat, tetapi kasus atau izin terbaru belum dapat dimuat. Muat ulang sebelum tindakan berikutnya.',
              ),
              uncertain: t(
                'The request result is not confirmed. Review the latest case and history before making a new change. Matching current values do not confirm who changed them.',
                'Hasil permintaan belum pasti. Tinjau kasus dan riwayat terbaru sebelum perubahan baru. Nilai yang sama bukan bukti siapa yang mengubahnya.',
              ),
              changed: t(
                'Review the current case and permissions before submitting again. Your unsent next action is kept only while you can access this case.',
                'Tinjau kasus dan izin terbaru sebelum mengirim kembali. Tulisan disimpan hanya selama akses kasus masih sah.',
              ),
              forbidden: t(
                'The action was not allowed. Your input is hidden while staff access is checked.',
                'Tindakan tidak diizinkan. Input disembunyikan selama pemeriksaan akses.',
              ),
              'invalid-close': t(
                'Choose an allowed closure reason, enter the outcome and next step, and complete the required duplicate lookup or follow-up evidence.',
                'Pilih alasan penutupan, isi hasil serta langkah berikutnya, dan lengkapi pencarian duplikat atau bukti tindak lanjut.',
              ),
              invalid: t(
                'Choose an allowed status and enter a next action of 1–2,000 characters.',
                'Pilih status yang diizinkan dan isi langkah berikutnya 1–2.000 karakter.',
              ),
            }[state.notice]
          }
        </p>
      ) : null}
      {busy ? (
        <p role="status">
          {state.phase === 'sending'
            ? t('Saving change…', 'Menyimpan perubahan…')
            : t('Checking the latest case…', 'Memeriksa kasus terbaru…')}
        </p>
      ) : null}
      {state.retryAt > now ? (
        <p role="status">
          {t(
            'Wait before another request:',
            'Tunggu sebelum permintaan berikutnya:',
          )}{' '}
          {Math.ceil((state.retryAt - now) / 1000)}s
        </p>
      ) : null}
      {detail && cap && detail.status !== 'closed' ? (
        <>
          <h3>{t('Manage this case', 'Kelola kasus ini')}</h3>
          {state.phase === 'review' ? (
            <Button variant="secondary" onClick={() => store.review()}>
              {t(
                'I have reviewed the current case',
                'Saya sudah meninjau kasus terbaru',
              )}
            </Button>
          ) : null}
          {cap.allowed_actions.includes('claim') &&
          cap.self_claim_membership_id === cap.membership_id ? (
            <Button disabled={disabled} onClick={() => void store.claim()}>
              {t('Take this case', 'Ambil kasus ini')}
            </Button>
          ) : null}
          {cap.active_conversation?.mode === 'waiting_staff' &&
          !cap.allowed_actions.includes('claim') ? (
            <p className={styles.help}>
              {t(
                'Taking the live conversation is a separate action; assigning the case does not accept the chat.',
                'Menerima percakapan langsung adalah tindakan terpisah; assignment tidak menerima chat.',
              )}
            </p>
          ) : null}
          {cap.allowed_actions.includes('assign') ? (
            <div className={styles.assignment}>
              <h4>{t('Assigned staff', 'Penanggung jawab')}</h4>
              <Button
                variant="secondary"
                disabled={disabled || state.directoryBusy}
                onClick={() => void store.directory()}
              >
                {t('Find eligible staff', 'Cari petugas yang berhak')}
              </Button>
              {state.directoryBusy ? (
                <p role="status">
                  {t('Loading eligible staff…', 'Memuat petugas…')}
                </p>
              ) : null}
              {state.directoryError ? (
                <p role="alert">
                  {t(
                    'The staff list is unavailable. Refresh it before choosing a person.',
                    'Daftar petugas belum tersedia. Muat ulang sebelum memilih.',
                  )}
                </p>
              ) : null}
              {state.directory ? (
                <>
                  <label htmlFor="eligible-staff">
                    {t('Eligible staff', 'Petugas yang berhak')}
                  </label>
                  <select
                    id="eligible-staff"
                    disabled={disabled}
                    value={state.target?.membership_id ?? ''}
                    onChange={(e) => store.choose(e.target.value)}
                  >
                    <option value="" disabled>
                      {t('Choose a staff member', 'Pilih petugas')}
                    </option>
                    {state.directory.items.map((v) => (
                      <option
                        key={v.membership_id}
                        value={v.membership_id}
                        disabled={
                          v.membership_id === detail.assigned_membership_id
                        }
                      >
                        {staffName(v)}
                      </option>
                    ))}
                  </select>
                  {!state.directory.items.length ? (
                    <p>
                      {t(
                        'No eligible staff on this page.',
                        'Tidak ada petugas yang berhak di halaman ini.',
                      )}
                    </p>
                  ) : null}
                  <div className={styles.row}>
                    <Button
                      variant="quiet"
                      disabled={disabled || state.directoryPage === 1}
                      onClick={() => void store.directory('previous')}
                    >
                      {t('Previous staff page', 'Halaman petugas sebelumnya')}
                    </Button>
                    <span>
                      {t('Page', 'Halaman')} {state.directoryPage}
                    </span>
                    <Button
                      variant="quiet"
                      disabled={disabled || !state.directory.has_more}
                      onClick={() => void store.directory('next')}
                    >
                      {t('Next staff page', 'Halaman petugas berikutnya')}
                    </Button>
                  </div>
                  <Button
                    disabled={disabled || !state.target}
                    onClick={() => store.requestAssignment()}
                  >
                    {detail.assigned_membership_id
                      ? t('Review reassignment', 'Tinjau pergantian petugas')
                      : t('Review assignment', 'Tinjau penugasan')}
                  </Button>
                </>
              ) : null}
              {cap.allowed_actions.includes('unassign') ? (
                <Button
                  variant="quiet"
                  disabled={disabled}
                  onClick={() => store.requestUnassign()}
                >
                  {t('Remove assignment', 'Hapus penugasan')}
                </Button>
              ) : null}
            </div>
          ) : null}
          {cap.allowed_actions.includes('update_status') &&
          cap.allowed_statuses.length ? (
            <form
              className={styles.form}
              onSubmit={(e) => {
                e.preventDefault();
                void store.status();
              }}
            >
              <h4>
                {t('Update handling status', 'Perbarui status penanganan')}
              </h4>
              <label htmlFor="handling-status">
                {t('Handling status', 'Status penanganan')}
              </label>
              <select
                id="handling-status"
                required
                disabled={disabled}
                value={state.status}
                onChange={(e) =>
                  store.edit({ status: e.target.value as WorkStatus })
                }
              >
                <option value="" disabled>
                  {t('Choose a status', 'Pilih status')}
                </option>
                {cap.allowed_statuses.map((s) => (
                  <option key={s} value={s}>
                    {label(s)}
                  </option>
                ))}
              </select>
              <label htmlFor="handling-next">
                {t(
                  'Next action for this case',
                  'Langkah berikutnya untuk kasus ini',
                )}
              </label>
              <textarea
                id="handling-next"
                required
                rows={4}
                disabled={busy}
                maxLength={2000}
                value={state.nextAction}
                aria-describedby="handling-help"
                aria-invalid={state.notice === 'invalid' || undefined}
                aria-errormessage={
                  state.notice === 'invalid'
                    ? 'staff-action-feedback'
                    : undefined
                }
                onChange={(e) => store.edit({ nextAction: e.target.value })}
              />
              <p id="handling-help" className={styles.help}>
                {t(
                  'Explain what is needed and who should act next. This records a case update; it does not send an email.',
                  'Jelaskan kebutuhan dan pihak berikutnya. Ini mencatat pembaruan kasus, bukan mengirim email.',
                )}
              </p>
              <Button type="submit" disabled={disabled}>
                {t('Save status', 'Simpan status')}
              </Button>
            </form>
          ) : null}
          {cap.allowed_actions.includes('close') &&
          cap.allowed_close_reasons.length ? (
            <form
              className={styles.form}
              onSubmit={(e) => {
                e.preventDefault();
                store.requestClose();
              }}
            >
              <h4>{t('Close this case', 'Tutup kasus ini')}</h4>
              <p className={styles.help}>
                {t(
                  'Record the outcome before closing. Staff cannot edit a closed case. This action does not send a customer email.',
                  'Catat hasil sebelum menutup. Petugas tidak dapat mengedit kasus tertutup. Tindakan ini tidak mengirim email pelanggan.',
                )}
              </p>
              <label htmlFor="close-reason">
                {t('Closure reason', 'Alasan penutupan')}
              </label>
              <select
                id="close-reason"
                required
                disabled={disabled}
                value={state.closeReason}
                onChange={(e) =>
                  store.edit({ closeReason: e.target.value as CloseReason })
                }
              >
                <option value="" disabled>
                  {t('Choose a reason', 'Pilih alasan')}
                </option>
                {cap.allowed_close_reasons.map((reason) => (
                  <option key={reason} value={reason}>
                    {closeLabel(reason)}
                  </option>
                ))}
              </select>
              <label htmlFor="close-resolution">
                {t('Outcome for the customer', 'Hasil untuk pelanggan')}
              </label>
              <textarea
                id="close-resolution"
                required
                rows={4}
                maxLength={4000}
                disabled={busy}
                value={state.resolution}
                onChange={(e) => store.edit({ resolution: e.target.value })}
                aria-describedby="close-help"
                aria-invalid={state.notice === 'invalid-close' || undefined}
                aria-errormessage={
                  state.notice === 'invalid-close'
                    ? 'staff-action-feedback'
                    : undefined
                }
              />
              <label htmlFor="close-next">
                {t('Next step after closure', 'Langkah setelah penutupan')}
              </label>
              <textarea
                id="close-next"
                required
                rows={3}
                maxLength={2000}
                disabled={busy}
                value={state.closeNext}
                onChange={(e) => store.edit({ closeNext: e.target.value })}
              />
              <p id="close-help" className={styles.help}>
                {t(
                  'The outcome and next step are visible in the case record. If nothing else is needed, say so.',
                  'Hasil dan langkah berikutnya terlihat pada catatan kasus. Jika tidak ada langkah tambahan, jelaskan.',
                )}
              </p>
              {state.closeReason === 'duplicate' ? (
                <>
                  <label htmlFor="duplicate-reference">
                    {t('Original case reference', 'Nomor kasus asli')}
                  </label>
                  <input
                    id="duplicate-reference"
                    required
                    disabled={disabled || state.lookup === 'loading'}
                    maxLength={37}
                    value={state.reference}
                    placeholder="CASE-�"
                    onChange={(e) => store.edit({ reference: e.target.value })}
                  />
                  <Button
                    variant="secondary"
                    disabled={disabled || state.lookup === 'loading'}
                    onClick={() => void store.findDuplicate()}
                  >
                    {t('Find original case', 'Cari kasus asli')}
                  </Button>
                  <p className={styles.help}>
                    {t(
                      'Enter the full case reference. Only an accessible case for the same customer can be selected.',
                      'Isi nomor kasus lengkap. Hanya kasus pelanggan yang sama dan dapat diakses yang bisa dipilih.',
                    )}
                  </p>
                  <p role={state.lookup === 'error' ? 'alert' : 'status'}>
                    {state.lookup === 'loading'
                      ? t('Checking case reference�', 'Memeriksa nomor kasus�')
                      : state.lookup === 'empty'
                        ? t(
                            'No eligible case found for this reference.',
                            'Tidak ditemukan kasus yang memenuhi syarat.',
                          )
                        : state.lookup === 'error'
                          ? t(
                              'The lookup could not be completed. Review access and try again.',
                              'Pencarian belum selesai. Periksa akses dan coba lagi.',
                            )
                          : state.candidate
                            ? t(
                                'Original case selected:',
                                'Kasus asli dipilih:',
                              ) +
                              ' ' +
                              state.candidate.public_reference
                            : ''}
                  </p>
                </>
              ) : null}
              {state.closeReason === 'no_response' ? (
                <>
                  <label htmlFor="close-evidence">
                    {t('Follow-up evidence', 'Bukti tindak lanjut')}
                  </label>
                  <textarea
                    id="close-evidence"
                    required
                    rows={4}
                    maxLength={2000}
                    disabled={busy}
                    value={state.evidence}
                    onChange={(e) => store.edit({ evidence: e.target.value })}
                  />
                  <p className={styles.help}>
                    {t(
                      'Record when and how you followed up, with a reference to the contact attempt.',
                      'Catat waktu, cara tindak lanjut, dan referensi upaya menghubungi pelanggan.',
                    )}
                  </p>
                </>
              ) : null}
              <Button
                type="submit"
                disabled={disabled || state.lookup === 'loading'}
              >
                {t('Review closure', 'Tinjau penutupan')}
              </Button>
            </form>
          ) : null}
        </>
      ) : null}
      {dirty && !busy ? (
        <Button variant="quiet" onClick={store.discard}>
          {t('Discard unsent changes', 'Buang perubahan yang belum dikirim')}
        </Button>
      ) : null}
      <Dialog
        open={state.closeConfirmation && !!detail && !!cap}
        title={t('Confirm case closure', 'Konfirmasi penutupan kasus')}
        closeLabel={t('Close confirmation', 'Tutup konfirmasi')}
        onClose={() => store.cancelClose()}
        actions={
          <>
            <Button variant="secondary" onClick={() => store.cancelClose()}>
              {t('Keep case open', 'Biarkan kasus terbuka')}
            </Button>
            <Button
              disabled={disabled}
              onClick={() => void store.confirmClose()}
            >
              {t('Close case', 'Tutup kasus')}
            </Button>
          </>
        }
      >
        <p>{detail?.public_reference}</p>
        <p>{detail?.summary}</p>
        <p>
          <strong>{closeLabel(state.closeReason)}</strong>
        </p>
        <p className={styles.preserve}>{state.resolution}</p>
        <p className={styles.preserve}>{state.closeNext}</p>
        {state.closeReason === 'duplicate' ? (
          <p>{state.candidate?.public_reference}</p>
        ) : null}
        {state.closeReason === 'no_response' ? (
          <p className={styles.preserve}>{state.evidence}</p>
        ) : null}
        <p>
          {t(
            'This closes the case and ends its active conversation. It does not send a customer email.',
            'Ini menutup kasus serta percakapan aktifnya. Tidak mengirim email pelanggan.',
          )}
        </p>
      </Dialog>
      <Dialog
        open={state.confirmation && !!detail && !!cap}
        title={t('Confirm assignment change', 'Konfirmasi perubahan penugasan')}
        closeLabel={t('Close confirmation', 'Tutup konfirmasi')}
        onClose={() => store.cancelConfirmation()}
        actions={
          <>
            <Button
              variant="secondary"
              onClick={() => store.cancelConfirmation()}
            >
              {t('Keep current assignment', 'Pertahankan penugasan')}
            </Button>
            <Button
              disabled={disabled}
              onClick={() => void store.confirmAssignment()}
            >
              {t('Confirm assignment', 'Konfirmasi penugasan')}
            </Button>
          </>
        }
      >
        <p>{detail?.public_reference}</p>
        <p>{detail?.summary}</p>
        <p>
          {t('From:', 'Dari:')}{' '}
          {cap?.assignee?.display_name ??
            (detail?.assigned_membership_id
              ? t('Current staff member', 'Petugas saat ini')
              : t('Unassigned', 'Belum ditugaskan'))}
        </p>
        <p>
          {t('To:', 'Ke:')}{' '}
          {state.target
            ? staffName(state.target)
            : t('Unassigned', 'Belum ditugaskan')}
        </p>
        <p>
          {t(
            'This changes case responsibility. It does not send a customer email.',
            'Ini mengubah penanggung jawab kasus, bukan mengirim email pelanggan.',
          )}
        </p>
      </Dialog>
    </section>
  );
}
