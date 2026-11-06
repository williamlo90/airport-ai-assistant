import {
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
import { useStaff } from '../staff/context';
import { useRouting } from '../../app/routing/context';
import { useUnsavedChanges } from '../../app/routing/draftState';
import { PageFrame } from '../../app/routing/pages';
import { Button } from '../../components/Button';
import { errorMessage } from '../../api/errors';
import { KnowledgeStore } from './store';
import { content, toContent } from './contracts';
import type { Content, Version } from './contracts';
import styles from './knowledge.module.css';
export default function KnowledgePage() {
  const staff = useStaff(),
    store = useMemo(() => (staff ? new KnowledgeStore(staff) : null), [staff]);
  useEffect(() => store?.connect(), [store]);
  return (
    <PageFrame staff>
      {store ? (
        <KnowledgePanel store={store} />
      ) : (
        <p role="alert">Staff sign-in required.</p>
      )}
    </PageFrame>
  );
}
export function KnowledgePanel({ store }: { store: KnowledgeStore }) {
  const state = useSyncExternalStore(store.subscribe, store.getState),
    staff = useSyncExternalStore(store.staff.subscribe, store.staff.getState),
    { locale } = useRouting(),
    t = (en: string, id: string) => (locale === 'en' ? en : id);
  const [mode, setMode] = useState<'create' | 'edit' | 'add' | null>(null),
    focus = useRef<HTMLElement>(null),
    scope = staff.memberships.find((m) => m.id === staff.selectedId),
    allowed = staff.ready && scope?.roles.includes('admin'),
    busy = ['loading', 'sending'].includes(state.phase),
    review = state.review;
  useEffect(() => {
    if (review || state.error) focus.current?.focus();
  }, [review, state.error]);
  useUnsavedChanges(
    !!review || state.phase === 'sending' || state.phase === 'unknown',
    () => {
      store.cancel();
      setMode(null);
    },
  );
  const editable = state.phase === 'ready' && !mode && !review;
  const closedMode = () => {
    setMode(null);
    store.cancel();
  };
  return (
    <section className={styles.page}>
      <h1>{t('Service information', 'Informasi layanan')}</h1>
      <p>
        {t(
          'Keep the information used by the assistant current. Drafts are not shared with customers.',
          'Perbarui informasi yang dipakai asisten. Draf tidak dibagikan kepada pelanggan.',
        )}
      </p>
      {!staff.ready ? (
        <p role="status">
          {t('Sign in with a staff account.', 'Masuk dengan akun petugas.')}
        </p>
      ) : (
        <label className={styles.field}>
          {t('Organization membership', 'Keanggotaan organisasi')}
          <select
            value={staff.selectedId ?? ''}
            disabled={busy || !!mode || !!review || state.phase === 'unknown'}
            onChange={(e) => {
              closedMode();
              store.staff.select(e.target.value);
            }}
          >
            <option value="" disabled>
              {t('Choose an organization', 'Pilih organisasi')}
            </option>
            {staff.memberships.map((m) => (
              <option key={m.id} value={m.id}>
                {m.organization_id}
                {m.roles.includes('admin') ? ' (admin)' : ''}
              </option>
            ))}
          </select>
        </label>
      )}
      {!allowed ? (
        <p role="alert">
          {t(
            'Administrator access is required for the selected organization.',
            'Akses administrator diperlukan untuk organisasi terpilih.',
          )}
        </p>
      ) : (
        <>
          {state.phase === 'idle' && (
            <Button onClick={() => void store.refresh()}>
              {t('Load service information', 'Muat informasi layanan')}
            </Button>
          )}
          {busy && (
            <p role="status">
              {state.phase === 'sending'
                ? t(
                    'Saving your reviewed action…',
                    'Menyimpan tindakan yang ditinjau…',
                  )
                : t(
                    'Reading current information…',
                    'Membaca informasi saat ini…',
                  )}
            </p>
          )}
          {state.error && (
            <section
              className={styles.notice}
              role="alert"
              ref={focus}
              tabIndex={-1}
            >
              <p>{errorMessage(state.error, locale)}</p>
              <p>
                {t(
                  'Read the current records before making a new change. A missing record does not prove an earlier request failed. Requests are never repeated automatically.',
                  'Baca catatan saat ini sebelum mengubahnya lagi. Catatan yang belum ditemukan tidak membuktikan permintaan sebelumnya gagal. Permintaan tidak diulang otomatis.',
                )}
              </p>
              <Button
                onClick={() => {
                  setMode(null);
                  void store.recover();
                }}
              >
                {t('Read current records', 'Baca catatan saat ini')}
              </Button>
            </section>
          )}
          {state.notice && (
            <p role="status" className={styles.notice}>
              {state.notice === 'saved'
                ? t(
                    'Your reviewed action was saved. Current records are shown below.',
                    'Tindakan yang ditinjau tersimpan. Catatan saat ini ditampilkan di bawah.',
                  )
                : t(
                    'These are the current records, not a receipt for the earlier request.',
                    'Ini catatan saat ini, bukan bukti penerimaan permintaan sebelumnya.',
                  )}
            </p>
          )}
          {state.page && (
            <>
              <div className={styles.actions}>
                <Button disabled={!editable} onClick={() => setMode('create')}>
                  {t('New service information', 'Informasi layanan baru')}
                </Button>
                <Button
                  variant="secondary"
                  disabled={!editable}
                  onClick={() => void store.refresh()}
                >
                  {t('Refresh list', 'Muat ulang daftar')}
                </Button>
              </div>
              <ul className={styles.list}>
                {state.page.items.map((d) => (
                  <li key={d.id}>
                    <div>
                      <strong>{d.title}</strong>
                      <p>{d.document_key}</p>
                    </div>
                    <Button
                      variant="secondary"
                      disabled={!editable}
                      onClick={() => void store.open(d.id)}
                    >
                      {t('Open information', 'Buka informasi')}
                    </Button>
                  </li>
                ))}
              </ul>
              {!state.page.items.length && (
                <p>
                  {t(
                    'No information found. If a save result is uncertain, read again before creating another record.',
                    'Informasi belum ditemukan. Jika hasil penyimpanan belum pasti, baca ulang sebelum membuat catatan lain.',
                  )}
                </p>
              )}
              <div className={styles.actions}>
                <Button
                  variant="secondary"
                  disabled={!editable || !state.canBack}
                  onClick={() => void store.previous()}
                >
                  {t('Previous', 'Sebelumnya')}
                </Button>
                <Button
                  variant="secondary"
                  disabled={!editable || !state.page.has_more}
                  onClick={() => void store.next()}
                >
                  {t('Next', 'Berikutnya')}
                </Button>
              </div>
            </>
          )}
          {state.document && (
            <>
              <h2>{state.document.title}</h2>
              <p>{state.document.document_key}</p>
              <label className={styles.field}>
                {t('Version', 'Versi')}
                <select
                  disabled={!editable}
                  value={state.version?.id ?? ''}
                  onChange={(e) => void store.selectVersion(e.target.value)}
                >
                  {state.versions?.items.map((v) => (
                    <option value={v.id} key={v.id}>
                      {v.version} ({v.status})
                    </option>
                  ))}
                  {state.version &&
                    !state.versions?.items.some(
                      (v) => v.id === state.version!.id,
                    ) && (
                      <option value={state.version.id}>
                        {state.version.version} ({state.version.status})
                      </option>
                    )}
                </select>
              </label>
              <div className={styles.actions}>
                <Button
                  variant="quiet"
                  disabled={!editable}
                  onClick={() => void store.firstVersions()}
                >
                  {t('First versions', 'Versi awal')}
                </Button>
                <Button
                  variant="quiet"
                  disabled={!editable || !state.versions?.has_more}
                  onClick={() => void store.nextVersions()}
                >
                  {t('More versions', 'Versi berikutnya')}
                </Button>
                <Button
                  variant="quiet"
                  disabled={!editable}
                  onClick={() => void store.refresh()}
                >
                  {t('Back to information list', 'Kembali ke daftar informasi')}
                </Button>
              </div>
              {state.version && (
                <article className={styles.panel}>
                  <p>
                    {t('Status', 'Status')}: {state.version.status}
                  </p>
                  <ContentView value={toContent(state.version)} />
                  <p>
                    {t('Search index', 'Indeks pencarian')}:{' '}
                    {state.index?.ready
                      ? t('Ready for this content', 'Siap untuk konten ini')
                      : t('Not ready', 'Belum siap')}
                  </p>
                  {!state.index?.indexing_available && (
                    <p>
                      {t(
                        'Indexing is not configured. Publication stays unavailable.',
                        'Pengindeksan belum dikonfigurasi. Publikasi belum tersedia.',
                      )}
                    </p>
                  )}
                  <div className={styles.actions}>
                    {state.version.status === 'draft' && (
                      <>
                        <Button
                          variant="secondary"
                          disabled={!editable}
                          onClick={() => setMode('edit')}
                        >
                          {t('Edit draft', 'Ubah draf')}
                        </Button>
                        <Button
                          variant="secondary"
                          disabled={
                            !editable ||
                            !state.index?.indexing_available ||
                            state.index.ready
                          }
                          onClick={() => store.review({ kind: 'index' })}
                        >
                          {t('Review search indexing', 'Tinjau pengindeksan')}
                        </Button>
                        <Button
                          disabled={
                            !editable ||
                            !state.index?.ready ||
                            !state.index.indexing_available
                          }
                          onClick={() => store.review({ kind: 'publish' })}
                        >
                          {t('Review publication', 'Tinjau publikasi')}
                        </Button>
                      </>
                    )}
                    <Button
                      variant="secondary"
                      disabled={!editable}
                      onClick={() => setMode('add')}
                    >
                      {t('New version', 'Versi baru')}
                    </Button>
                  </div>
                  {state.version.status === 'published' && editable && (
                    <Withdraw key={state.version.id} store={store} />
                  )}
                </article>
              )}
            </>
          )}
          {mode && ['ready', 'review'].includes(state.phase) && (
            <div hidden={!!review}>
              <Editor
                key={
                  (staff.selectedId ?? '') +
                  ':' +
                  mode +
                  ':' +
                  (state.version?.id ?? 'new')
                }
                mode={mode}
                initial={state.version}
                store={store}
                onCancel={closedMode}
              />
            </div>
          )}
          {review && (
            <section
              className={styles.review}
              ref={focus}
              tabIndex={-1}
              aria-label={t(
                'Review information change',
                'Tinjau perubahan informasi',
              )}
            >
              <h2>
                {t('Review information change', 'Tinjau perubahan informasi')}
              </h2>
              <p>
                {t('Organization', 'Organisasi')}: {scope!.organization_id}
              </p>
              <p>
                {review.document?.title ??
                  ('title' in review.command ? review.command.title : '')}
              </p>
              <p>
                {
                  {
                    create: t('Create a draft', 'Buat draf'),
                    edit: t('Update this draft', 'Perbarui draf ini'),
                    add: t(
                      'Add a new draft version',
                      'Tambahkan versi draf baru',
                    ),
                    index: t(
                      'Prepare the search index',
                      'Siapkan indeks pencarian',
                    ),
                    publish: t(
                      'Publish for the configured audience',
                      'Publikasikan untuk audiens yang ditentukan',
                    ),
                    withdraw: t(
                      'Withdraw this published version',
                      'Tarik versi yang dipublikasikan',
                    ),
                  }[review.command.kind]
                }
              </p>
              {review.version && (
                <details>
                  <summary>{t('Current version', 'Versi saat ini')}</summary>
                  <ContentView value={toContent(review.version)} />
                </details>
              )}
              {'content' in review.command && (
                <>
                  <h3>
                    {t('Proposed information', 'Informasi yang diusulkan')}
                  </h3>
                  <ContentView value={review.command.content} />
                </>
              )}
              {review.command.kind === 'withdraw' && (
                <p>{review.command.reason}</p>
              )}
              {review.command.kind === 'publish' && (
                <p>
                  {t(
                    'Customers may receive this version after publication. The server checks the current index and validity.',
                    'Pelanggan dapat menerima versi ini setelah publikasi. Server memeriksa indeks dan masa berlaku saat ini.',
                  )}
                </p>
              )}
              <div className={styles.actions}>
                <Button
                  onClick={() => {
                    setMode(null);
                    void store.confirm();
                  }}
                >
                  {t(
                    'Confirm reviewed action',
                    'Konfirmasi tindakan yang ditinjau',
                  )}
                </Button>
                <Button variant="secondary" onClick={() => store.cancel()}>
                  {t('Back without saving', 'Kembali tanpa menyimpan')}
                </Button>
              </div>
            </section>
          )}
        </>
      )}
    </section>
  );
}
function ContentView({ value: v }: { value: Content }) {
  const { locale } = useRouting(),
    t = (en: string, id: string) => (locale === 'en' ? en : id);
  return (
    <dl className={styles.content}>
      {[
        [t('Information', 'Informasi'), v.body],
        [t('Source', 'Sumber'), v.source_uri],
        [t('Service', 'Layanan'), v.service_type],
        [t('Language', 'Bahasa'), v.language],
        [t('Audience', 'Audiens'), v.visibility],
        [t('Staff roles', 'Peran petugas'), v.allowed_roles.join(', ') || '-'],
        [t('Valid from (UTC)', 'Berlaku sejak (UTC)'), v.valid_from],
        [t('Valid until (UTC)', 'Berlaku hingga (UTC)'), v.valid_to ?? '-'],
        [t('Terminal', 'Terminal'), v.applicability.terminal ?? '-'],
        [
          t('Booking created from (UTC)', 'Booking dibuat sejak (UTC)'),
          v.applicability.booking_created_from ?? '-',
        ],
        [
          t('Booking created until (UTC)', 'Booking dibuat hingga (UTC)'),
          v.applicability.booking_created_to ?? '-',
        ],
        [
          t('Policy reference', 'Referensi kebijakan'),
          v.policy_reference ?? '-',
        ],
        [
          t('Previous version reference', 'Referensi versi sebelumnya'),
          v.supersedes_version_id ?? '-',
        ],
      ].map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}
function Withdraw({ store }: { store: KnowledgeStore }) {
  const [reason, setReason] = useState(''),
    { locale } = useRouting(),
    t = (en: string, id: string) => (locale === 'en' ? en : id);
  useUnsavedChanges(!!reason, () => setReason(''));
  return (
    <div>
      <label className={styles.field}>
        {t('Withdrawal reason', 'Alasan penarikan')}
        <textarea
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          maxLength={2000}
        />
      </label>
      <Button
        variant="secondary"
        disabled={!reason.trim() || Array.from(reason).length > 1000}
        onClick={() => store.review({ kind: 'withdraw', reason })}
      >
        {t('Review withdrawal', 'Tinjau penarikan')}
      </Button>
    </div>
  );
}
function Editor({
  mode,
  initial,
  store,
  onCancel,
}: {
  mode: 'create' | 'edit' | 'add';
  initial: Version | null;
  store: KnowledgeStore;
  onCancel: () => void;
}) {
  const { locale } = useRouting(),
    t = (en: string, id: string) => (locale === 'en' ? en : id),
    [dirty, setDirty] = useState(false),
    [error, setError] = useState(false),
    focus = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (error) focus.current?.focus();
  }, [error]);
  useUnsavedChanges(dirty, onCancel);
  const base =
    mode === 'create' || !initial
      ? {
          body: '',
          source_uri: 'urn:airport:',
          service_type: 'general',
          language: locale,
          visibility: 'public',
          allowed_roles: [],
          valid_from: new Date().toISOString(),
          valid_to: null,
          applicability: {
            terminal: null,
            booking_created_from: null,
            booking_created_to: null,
          },
          policy_reference: null,
          supersedes_version_id: null,
        }
      : toContent(initial);
  const utc = (v: string | null) =>
    v ? new Date(v).toISOString().slice(0, -1) : '';
  return (
    <form
      className={styles.panel}
      onChange={() => setDirty(true)}
      onSubmit={(e) => {
        e.preventDefault();
        const f = new FormData(e.currentTarget),
          read = (k: string) => String(f.get(k) ?? ''),
          date = (k: string) => {
            const original: Record<string, string | null> = {
              valid_from: base.valid_from,
              valid_to: base.valid_to,
              booking_from: base.applicability.booking_created_from,
              booking_to: base.applicability.booking_created_to,
            };
            return read(k) === utc(original[k] ?? null) ||
              (!!read(k) &&
                !!original[k] &&
                Date.parse(read(k) + 'Z') === Date.parse(original[k]!))
              ? (original[k] ?? null)
              : read(k)
                ? new Date(read(k) + 'Z').toISOString()
                : null;
          };
        const proposed = {
          body: read('body'),
          source_uri: read('source_uri'),
          service_type: read('service_type'),
          language: read('language'),
          visibility: read('visibility'),
          allowed_roles: f.getAll('allowed_roles'),
          valid_from: date('valid_from'),
          valid_to: date('valid_to'),
          applicability: {
            terminal: read('terminal') || null,
            booking_created_from: date('booking_from'),
            booking_created_to: date('booking_to'),
          },
          policy_reference: read('policy_reference') || null,
          supersedes_version_id:
            mode === 'add' ? (initial?.id ?? null) : base.supersedes_version_id,
        };
        const parsed = content.safeParse(proposed);
        const title = read('title'),
          documentKey = read('document_key');
        if (
          !parsed.success ||
          (mode === 'create' &&
            (!title.trim() ||
              Array.from(title).length > 200 ||
              !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(documentKey) ||
              documentKey.length > 100))
        ) {
          setError(true);
          return;
        }
        setError(false);
        store.review(
          mode === 'create'
            ? {
                kind: 'create',
                title,
                document_key: documentKey,
                content: parsed.data,
              }
            : { kind: mode, content: parsed.data },
        );
      }}
    >
      <h2>
        {mode === 'create'
          ? t('New service information', 'Informasi layanan baru')
          : mode === 'edit'
            ? t('Edit draft', 'Ubah draf')
            : t('New version', 'Versi baru')}
      </h2>
      {mode === 'create' && (
        <>
          <label className={styles.field}>
            {t('Title', 'Judul')}
            <input name="title" maxLength={400} required />
          </label>
          <label className={styles.field}>
            {t(
              'Stable reference (lowercase words separated by hyphens)',
              'Referensi tetap (huruf kecil dipisahkan tanda hubung)',
            )}
            <input name="document_key" maxLength={100} required />
          </label>
        </>
      )}
      <label className={styles.field}>
        {t('Information text', 'Teks informasi')}
        <textarea
          name="body"
          defaultValue={base.body}
          maxLength={40000}
          rows={7}
          required
        />
      </label>
      <label className={styles.field}>
        {t(
          'Official source (HTTPS or managed airport reference)',
          'Sumber resmi (HTTPS atau referensi bandara)',
        )}
        <input
          name="source_uri"
          defaultValue={base.source_uri}
          maxLength={2048}
          required
        />
      </label>
      <div className={styles.columns}>
        <label className={styles.field}>
          {t('Service', 'Layanan')}
          <select name="service_type" defaultValue={base.service_type}>
            {['general', 'parking', 'lounge'].map((v) => (
              <option key={v}>{v}</option>
            ))}
          </select>
        </label>
        <label className={styles.field}>
          {t('Language', 'Bahasa')}
          <select name="language" defaultValue={base.language}>
            <option value="en">English</option>
            <option value="id">Bahasa Indonesia</option>
          </select>
        </label>
        <label className={styles.field}>
          {t('Audience', 'Audiens')}
          <select name="visibility" defaultValue={base.visibility}>
            <option value="public">{t('Public', 'Publik')}</option>
            <option value="staff">{t('Staff only', 'Khusus petugas')}</option>
          </select>
        </label>
      </div>
      <fieldset>
        <legend>
          {t(
            'Staff roles (only for staff-only information)',
            'Peran petugas (hanya untuk informasi khusus petugas)',
          )}
        </legend>
        {['admin', 'cs', 'supervisor'].map((r) => (
          <label className={styles.check} key={r}>
            <input
              type="checkbox"
              name="allowed_roles"
              value={r}
              defaultChecked={base.allowed_roles.some((x) => x === r)}
            />
            {r}
          </label>
        ))}
      </fieldset>
      <div className={styles.columns}>
        {[
          [
            'valid_from',
            t('Valid from (UTC)', 'Berlaku sejak (UTC)'),
            base.valid_from,
          ],
          [
            'valid_to',
            t('Valid until (UTC, optional)', 'Berlaku hingga (UTC, opsional)'),
            base.valid_to,
          ],
          [
            'booking_from',
            t(
              'Booking created from (UTC, optional)',
              'Booking dibuat sejak (UTC, opsional)',
            ),
            base.applicability.booking_created_from,
          ],
          [
            'booking_to',
            t(
              'Booking created until (UTC, optional)',
              'Booking dibuat hingga (UTC, opsional)',
            ),
            base.applicability.booking_created_to,
          ],
        ].map(([name, label, value]) => (
          <label className={styles.field} key={name!}>
            {label}
            <input
              type="datetime-local"
              step="any"
              name={name!}
              required={name === 'valid_from'}
              defaultValue={utc(value ?? null)}
            />
          </label>
        ))}
      </div>
      <label className={styles.field}>
        {t('Terminal (optional)', 'Terminal (opsional)')}
        <input
          name="terminal"
          defaultValue={base.applicability.terminal ?? ''}
          maxLength={32}
        />
      </label>
      <label className={styles.field}>
        {t('Policy reference (optional)', 'Referensi kebijakan (opsional)')}
        <input
          name="policy_reference"
          defaultValue={base.policy_reference ?? ''}
          maxLength={200}
        />
      </label>
      {error && (
        <p role="alert" tabIndex={-1} ref={focus}>
          {t(
            'Check the text, reference, source, audience/roles and UTC date ranges. Text must contain 1–20,000 Unicode characters.',
            'Periksa teks, referensi, sumber, audiens/peran dan rentang waktu UTC. Teks harus berisi 1–20.000 karakter Unicode.',
          )}
        </p>
      )}
      <div className={styles.actions}>
        <Button type="submit">
          {t('Review information', 'Tinjau informasi')}
        </Button>
        <Button variant="secondary" onClick={onCancel}>
          {t('Discard edits', 'Buang perubahan')}
        </Button>
      </div>
    </form>
  );
}
