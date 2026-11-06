import { useEffect, useState } from 'react';
import type { AuthCoordinator } from '../../app/auth/coordinator';
import { useAuthState } from '../../app/auth/context';
import { useRouting } from '../../app/routing/context';
import { Button } from '../../components/Button';
import { decodeAttachmentList } from './attachment-contracts';
import styles from './cases.module.css';
type Snapshot = {
  key: string;
  items: ReturnType<typeof decodeAttachmentList> | null;
  error: boolean;
};

export function CaseAttachments({
  auth,
  caseId,
}: {
  auth: AuthCoordinator;
  caseId: string;
}) {
  const session = useAuthState(auth),
    { locale } = useRouting(),
    [attempt, retry] = useState(0);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const key = `${caseId}:${session.epoch}:${attempt}`;
  const t = (en: string, id: string) => (locale === 'en' ? en : id);
  useEffect(() => {
    if (session.phase !== 'ready') return;
    let owner;
    try {
      owner = auth.capture();
    } catch {
      return;
    }
    let current = true;
    const control = new AbortController();
    const off = auth.onSourceUnavailable(() => {
      current = false;
      control.abort();
      setSnapshot(null);
    });
    void auth
      .readResource({
        path: `/cases/${caseId}/attachments`,
        method: 'GET',
        boundary: owner,
        isRelevant: () => current,
        signal: control.signal,
        successStatus: 200,
        decode: decodeAttachmentList,
      })
      .then((items) => {
        if (current && auth.isCurrent(owner))
          setSnapshot({ key, items, error: false });
      })
      .catch(() => {
        if (current) setSnapshot({ key, items: null, error: true });
      });
    return () => {
      current = false;
      control.abort();
      off();
    };
  }, [auth, caseId, session.phase, session.epoch, key]);
  const value =
    snapshot?.key === key && session.phase === 'ready' ? snapshot : null;
  return (
    <section
      className={styles.section}
      aria-labelledby="case-attachments-title"
    >
      <h2 id="case-attachments-title">{t('Attachments', 'Lampiran')}</h2>
      {!value ? (
        <p role="status">{t('Loading attachments…', 'Memuat lampiran…')}</p>
      ) : value.error ? (
        <>
          <p role="alert">
            {t(
              'Attachments could not be read.',
              'Lampiran belum dapat dibaca.',
            )}
          </p>
          <Button variant="secondary" onClick={() => retry((n) => n + 1)}>
            {t('Read attachments again', 'Baca lampiran kembali')}
          </Button>
        </>
      ) : value.items?.length ? (
        <ul className={styles.caseList}>
          {value.items.map((item, i) => (
            <li key={item.id}>
              <a href={`/api/v1/attachments/${item.id}/download`} download>
                {t('Download attachment', 'Unduh lampiran')} {i + 1}
              </a>
              <p className={styles.help}>
                {item.mime_type} ·{' '}
                {new Intl.NumberFormat(locale).format(item.byte_size)}{' '}
                {t('bytes', 'bita')}
              </p>
            </li>
          ))}
        </ul>
      ) : (
        <p>
          {t(
            'No attachments were included.',
            'Tidak ada lampiran yang disertakan.',
          )}
        </p>
      )}
    </section>
  );
}
