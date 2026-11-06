import { useEffect, useRef, useState } from 'react';
import { useAuthState } from '../../app/auth/context';
import { useRouting } from '../../app/routing/context';
import { useUnsavedChanges } from '../../app/routing/draftState';
import { Button } from '../../components/Button';
import type { CaseDraftStore } from './store';
import type { DraftView } from './contracts';
import { decodeAttachmentCapabilities } from './attachment-contracts';
import styles from './cases.module.css';

export function DraftAttachments({
  store,
  draft,
  disabled,
}: {
  store: CaseDraftStore;
  draft: DraftView;
  disabled: boolean;
}) {
  const { locale } = useRouting(),
    auth = useAuthState(store.auth);
  const kind = auth.phase === 'ready' ? auth.kind : null;
  const [enabled, setEnabled] = useState(false),
    [checked, setChecked] = useState(false),
    [file, setFile] = useState<File | null>(null);
  const input = useRef<HTMLInputElement>(null),
    t = (en: string, id: string) => (locale === 'en' ? en : id);
  useEffect(() => {
    if (auth.phase !== 'ready' || kind !== 'customer') return;
    const control = new AbortController();
    let owner;
    try {
      owner = store.auth.capture();
    } catch {
      return;
    }
    let current = true;
    void store.auth
      .readResource({
        path: '/attachments/capabilities',
        method: 'GET',
        boundary: owner,
        isRelevant: () => current,
        signal: control.signal,
        successStatus: 200,
        decode: decodeAttachmentCapabilities,
      })
      .then((capabilities) => {
        if (current && store.auth.isCurrent(owner)) {
          setEnabled(capabilities.upload_enabled);
          setChecked(true);
        }
      })
      .catch(() => {
        if (current) {
          setEnabled(false);
          setChecked(true);
        }
      });
    return () => {
      current = false;
      control.abort();
    };
  }, [store, auth.phase, auth.epoch, kind]);
  const discard = () => {
    setFile(null);
    store.fileSelection(false);
    if (input.current) input.current.value = '';
  };
  useUnsavedChanges(!!file, discard);
  const invalid =
    file !== null &&
    (file.size < 1 ||
      file.size > 5242880 ||
      !['image/png', 'image/jpeg', 'application/pdf'].includes(file.type));
  return (
    <section
      className={styles.section}
      aria-labelledby="draft-attachments-title"
    >
      <h2 id="draft-attachments-title">{t('Attachments', 'Lampiran')}</h2>
      <p className={styles.help}>
        {t(
          'Optional · PNG, JPEG or PDF · up to 5 MiB each · 3 files per request. Files are checked before being added to your preview.',
          'Opsional · PNG, JPEG atau PDF · maksimal 5 MiB per file · 3 file per permintaan. File diperiksa sebelum ditambahkan ke tinjauan.',
        )}
      </p>
      {draft.payload.attachment_ids.length > 0 && (
        <ul className={styles.caseList}>
          {draft.payload.attachment_ids.map((id, i) => (
            <li key={id}>
              <a href={`/api/v1/attachments/${id}/download`} download>
                {t('Download attachment', 'Unduh lampiran')} {i + 1}
              </a>
              <Button
                variant="quiet"
                disabled={disabled}
                aria-label={`${t('Remove attachment', 'Hapus lampiran')} ${i + 1}`}
                onClick={() => {
                  void store.removeAttachment(id).catch(() => {});
                }}
              >
                {t('Remove', 'Hapus')}
              </Button>
            </li>
          ))}
        </ul>
      )}
      {!checked ? (
        <p role="status">
          {t(
            'Checking file upload availability…',
            'Memeriksa ketersediaan unggah…',
          )}
        </p>
      ) : !enabled ? (
        <p role="status">
          {t(
            'File uploads are unavailable. You can send this request without attachments.',
            'Unggah file tidak tersedia. Anda dapat mengirim permintaan tanpa lampiran.',
          )}
        </p>
      ) : (
        <form
          className={styles.form}
          onSubmit={(e) => {
            e.preventDefault();
            if (file && !invalid) {
              const selected = file;
              discard();
              void store.upload(selected).catch(() => {});
            }
          }}
        >
          <fieldset
            disabled={disabled || draft.payload.attachment_ids.length >= 3}
          >
            <label htmlFor="draft-attachment-file">
              {t('Choose a file', 'Pilih file')}
            </label>
            <input
              ref={input}
              id="draft-attachment-file"
              type="file"
              accept="image/png,image/jpeg,application/pdf"
              aria-describedby="draft-attachment-help"
              onChange={(e) => {
                const file = e.target.files?.[0] ?? null;
                setFile(file);
                store.fileSelection(!!file);
              }}
            />
            <p
              id="draft-attachment-help"
              className={invalid ? styles.fieldError : styles.help}
            >
              {invalid
                ? t(
                    'Choose a non-empty PNG, JPEG or PDF no larger than 5 MiB.',
                    'Pilih PNG, JPEG atau PDF yang tidak kosong dan maksimal 5 MiB.',
                  )
                : t(
                    'Upload one file at a time. Check the updated preview before sending your request.',
                    'Unggah satu file setiap kali. Tinjau preview terbaru sebelum mengirim permintaan.',
                  )}
            </p>
            <Button
              type="submit"
              variant="secondary"
              disabled={!file || invalid}
            >
              {t('Add file to preview', 'Tambahkan file ke tinjauan')}
            </Button>
            {file && (
              <Button variant="quiet" onClick={discard}>
                {t('Clear selected file', 'Batalkan pilihan file')}
              </Button>
            )}
          </fieldset>
        </form>
      )}
    </section>
  );
}
