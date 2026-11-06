import type { StaffStore } from '../staff/store';
import { ApiFailure } from '../../api/errors';
import type { BoundarySnapshot } from '../../api/contracts';
import {
  content,
  document,
  directory,
  versions,
  version,
  indexProof,
  indexStatus,
  key,
} from './contracts';
import type {
  Content,
  Document,
  Version,
  Directory,
  Versions,
  IndexStatus,
} from './contracts';
export type Command =
  | { kind: 'create'; title: string; document_key: string; content: Content }
  | { kind: 'edit' | 'add'; content: Content }
  | { kind: 'index' | 'publish' }
  | { kind: 'withdraw'; reason: string };
interface Review {
  command: Command;
  document: Document | null;
  version: Version | null;
}
interface State {
  index: IndexStatus | null;
  phase:
    | 'idle'
    | 'loading'
    | 'ready'
    | 'review'
    | 'sending'
    | 'unknown'
    | 'unavailable';
  page: Directory | null;
  document: Document | null;
  versions: Versions | null;
  version: Version | null;
  review: Review | null;
  error: ApiFailure | null;
  notice: 'saved' | 'current' | null;
  retryAt: number;
  canBack: boolean;
}
const empty = (): State => ({
  index: null,
  phase: 'idle',
  page: null,
  document: null,
  versions: null,
  version: null,
  review: null,
  error: null,
  notice: null,
  retryAt: 0,
  canBack: false,
});
export class KnowledgeStore {
  private state = empty();
  private serial = 0;
  private control: AbortController | null = null;
  private fingerprint = '';
  private docId: string | null = null;
  private versionId: string | null = null;
  private creationKey: string | null = null;
  private positions: (string | null)[] = [null];
  private listeners = new Set<() => void>();
  constructor(readonly staff: StaffStore) {}
  getState = () => this.state;
  subscribe = (f: () => void) => {
    this.listeners.add(f);
    return () => {
      this.listeners.delete(f);
    };
  };
  private set(v: Partial<State>) {
    this.state = { ...this.state, ...v };
    this.listeners.forEach((f) => f());
  }
  clear() {
    this.serial++;
    this.control?.abort();
    this.docId = null;
    this.versionId = null;
    this.creationKey = null;
    this.positions = [null];
    this.set(empty());
  }
  private scope() {
    const s = this.staff.captureScope();
    if (!s?.membership.roles.includes('admin'))
      throw new ApiFailure('invalid-request', 'not-sent');
    return s;
  }
  connect() {
    const sync = () => {
      let fp = '';
      try {
        const s = this.scope();
        fp = JSON.stringify([
          s.owner.authEpoch,
          s.owner.principalId,
          s.membership,
        ]);
      } catch {
        /* closed */
      }
      if (fp !== this.fingerprint) {
        this.clear();
        this.fingerprint = fp;
      }
    };
    const off = this.staff.subscribe(sync),
      reset = this.staff.auth.onReset(() => {
        this.clear();
        this.fingerprint = '';
      });
    sync();
    return () => {
      off();
      reset();
      this.clear();
    };
  }
  private current(n: number, owner: BoundarySnapshot, mid: string) {
    try {
      return (
        this.serial === n &&
        this.staff.auth.isCurrent(owner) &&
        this.scope().membership.id === mid
      );
    } catch {
      return false;
    }
  }
  private path = '/admin/knowledge';
  private request<T>(
    path: string,
    decode: (v: unknown) => T,
    owner: BoundarySnapshot,
    n: number,
    mid: string,
    method: 'GET' | 'POST' | 'PATCH' = 'GET',
    body?: unknown,
    status = 200,
  ) {
    return this.staff.auth.request({
      path: this.path + path,
      method,
      decode,
      boundary: owner,
      successStatus: status,
      isRelevant: () => this.current(n, owner, mid),
      signal: this.control!.signal,
      ...(body === undefined ? {} : { body }),
    });
  }
  async refresh() {
    if (['sending', 'loading'].includes(this.state.phase)) return;
    this.docId = null;
    this.versionId = null;
    this.creationKey = null;
    this.positions = [null];
    await this.load(false);
  }
  async next() {
    if (this.state.phase !== 'ready' || !this.state.page?.next_after) return;
    this.positions.push(this.state.page.next_after);
    await this.load(false);
  }
  async previous() {
    if (this.state.phase !== 'ready' || this.positions.length <= 1) return;
    this.positions.pop();
    await this.load(false);
  }
  async open(id: string) {
    if (
      this.state.phase !== 'ready' ||
      !this.state.page?.items.some((d) => d.id === id)
    )
      return;
    this.docId = id;
    this.versionId = null;
    await this.load(false);
  }
  async recover() {
    await this.load(true);
  }
  async selectVersion(id: string) {
    if (
      this.state.phase !== 'ready' ||
      !this.state.versions?.items.some((v) => v.id === id)
    )
      return;
    this.versionId = id;
    await this.load(false);
  }
  async nextVersions() {
    if (this.state.phase !== 'ready' || !this.state.versions?.next_after)
      return;
    await this.load(false, this.state.versions.next_after);
  }
  async firstVersions() {
    await this.load(false, 0);
  }
  private async load(recovery: boolean, afterVersion = 0) {
    if (
      ['sending', 'loading'].includes(this.state.phase) ||
      Date.now() < this.state.retryAt
    )
      return;
    const n = ++this.serial;
    this.control?.abort();
    this.control = new AbortController();
    this.set({
      index: null,
      phase: 'loading',
      page: null,
      document: null,
      versions: null,
      version: null,
      review: null,
      error: null,
      notice: null,
    });
    try {
      const s = this.scope(),
        mid = s.membership.id,
        org = s.membership.organization_id;
      if (this.docId) {
        const doc = await this.request(
          '/documents/' + this.docId + '?membership_id=' + mid,
          (v) => {
            const d = document.parse(v);
            if (d.id !== this.docId || d.organization_id !== org) throw Error();
            return d;
          },
          s.owner,
          n,
          mid,
        );
        const page = await this.request(
          '/documents/' +
            doc.id +
            '/versions?membership_id=' +
            mid +
            '&limit=20&after=' +
            afterVersion,
          (v) => {
            const p = versions.parse(v);
            if (
              p.has_more !== (p.next_after !== null) ||
              p.items.some(
                (v, i) =>
                  v.document_id !== doc.id ||
                  v.version <= afterVersion ||
                  (v.visibility === 'staff' && v.organization_id !== org) ||
                  (i > 0 && p.items[i - 1]!.version >= v.version),
              ) ||
              (p.has_more &&
                (p.items.length !== 20 ||
                  p.next_after !== p.items.at(-1)?.version))
            )
              throw Error();
            return p;
          },
          s.owner,
          n,
          mid,
        );
        let selected =
          page.items.find((v) => v.id === this.versionId) ??
          page.items[0] ??
          null;
        if (this.versionId && !page.items.some((v) => v.id === this.versionId))
          selected = await this.request(
            '/versions/' + this.versionId + '?membership_id=' + mid,
            (v) => {
              const r = version.parse(v);
              if (
                r.id !== this.versionId ||
                r.document_id !== doc.id ||
                (r.visibility === 'staff' && r.organization_id !== org)
              )
                throw Error();
              return r;
            },
            s.owner,
            n,
            mid,
          );
        const status = selected
          ? await this.request(
              '/versions/' + selected.id + '/index?membership_id=' + mid,
              (v) => {
                const p = indexStatus.parse(v);
                if (
                  p.version_id !== selected!.id ||
                  p.basis_hash !== selected!.basis_hash
                )
                  throw Error();
                return p;
              },
              s.owner,
              n,
              mid,
            )
          : null;
        if (!this.current(n, s.owner, mid)) return;
        this.versionId = selected?.id ?? null;
        this.set({
          phase: 'ready',
          document: doc,
          versions: page,
          version: selected,
          index: status,
          notice: recovery ? 'current' : null,
          retryAt: 0,
        });
      } else {
        const after = this.positions.at(-1),
          q = new URLSearchParams({ membership_id: mid, limit: '25' });
        if (after && !this.creationKey) q.set('after', after);
        if (this.creationKey) q.set('document_key', this.creationKey);
        const page = await this.request(
          '/documents?' + q,
          (v) => {
            const p = directory.parse(v);
            if (
              p.membership_id !== mid ||
              p.organization_id !== org ||
              p.has_more !== (p.next_after !== null) ||
              p.items.some(
                (d, i) =>
                  d.organization_id !== org ||
                  (!!after && d.id <= after) ||
                  (!!this.creationKey && d.document_key !== this.creationKey) ||
                  (i > 0 && p.items[i - 1]!.id >= d.id),
              ) ||
              (p.has_more &&
                (p.items.length !== 25 || p.next_after !== p.items.at(-1)?.id))
            )
              throw Error();
            return p;
          },
          s.owner,
          n,
          mid,
        );
        if (!this.current(n, s.owner, mid)) return;
        this.set({
          phase: 'ready',
          page,
          notice: recovery ? 'current' : null,
          canBack: this.positions.length > 1,
          retryAt: 0,
        });
      }
    } catch (e) {
      this.fail(e, n, false);
    }
  }
  review(command: Command) {
    if (this.state.phase !== 'ready') return;
    const c = structuredClone(command),
      d = this.state.document,
      v = this.state.version;
    if (
      c.kind === 'create' &&
      (!key.safeParse(c.document_key).success ||
        !c.title.trim() ||
        Array.from(c.title).length > 200 ||
        !content.safeParse(c.content).success)
    )
      return;
    if (
      ['edit', 'add'].includes(c.kind) &&
      (!d ||
        !v ||
        !content.safeParse('content' in c ? c.content : null).success)
    )
      return;
    if (
      (c.kind === 'edit' && v?.status !== 'draft') ||
      ((c.kind === 'publish' || c.kind === 'index') && v?.status !== 'draft') ||
      (c.kind === 'withdraw' &&
        (v?.status !== 'published' ||
          !c.reason.trim() ||
          Array.from(c.reason).length > 1000))
    )
      return;
    this.set({
      phase: 'review',
      review: { command: c, document: d, version: v },
      notice: null,
    });
  }
  cancel() {
    if (this.state.phase === 'review')
      this.set({ phase: 'ready', review: null });
  }
  async confirm() {
    const r = this.state.review;
    if (this.state.phase !== 'review' || !r || Date.now() < this.state.retryAt)
      return;
    const n = ++this.serial;
    this.control = new AbortController();
    this.set({
      index: null,
      phase: 'sending',
      review: null,
      error: null,
      page: null,
      document: null,
      versions: null,
      version: null,
      notice: null,
    });
    try {
      const s = this.scope(),
        mid = s.membership.id,
        c = r.command;
      let path = '',
        method: 'POST' | 'PATCH' = 'POST',
        body: unknown,
        status = 200;
      if (c.kind === 'create') {
        this.creationKey = c.document_key;
        this.positions = [null];
        path = '/documents';
        status = 201;
        body = {
          membership_id: mid,
          title: c.title,
          document_key: c.document_key,
          content: c.content,
        };
      } else if (c.kind === 'add') {
        path = '/documents/' + r.document!.id + '/versions';
        status = 201;
        body = {
          membership_id: mid,
          expected_document_revision: r.document!.revision,
          content: c.content,
        };
        this.versionId = null;
      } else if (c.kind === 'edit') {
        path = '/versions/' + r.version!.id;
        method = 'PATCH';
        body = {
          membership_id: mid,
          expected_revision: r.version!.revision,
          content: c.content,
        };
      } else {
        path = '/versions/' + r.version!.id + '/' + c.kind;
        body = {
          membership_id: mid,
          expected_revision: r.version!.revision,
          ...(c.kind === 'withdraw' ? { reason: c.reason } : {}),
        };
      }
      const saved = await this.request(
        path,
        (v) => {
          if (c.kind === 'create') {
            const d = document.parse(v);
            if (
              d.organization_id !== s.membership.organization_id ||
              d.document_key !== c.document_key ||
              d.title !== c.title
            )
              throw Error();
            return d;
          }
          if (c.kind === 'index') {
            const p = indexProof.parse(v);
            if (
              p.version_id !== r.version!.id ||
              p.basis_hash !== r.version!.basis_hash ||
              Date.parse(p.valid_until) <= Date.now()
            )
              throw Error();
            return p;
          }
          const v2 = version.parse(v);
          if (
            v2.document_id !== r.document!.id ||
            (c.kind !== 'add' && v2.id !== r.version!.id) ||
            (v2.visibility === 'staff' &&
              v2.organization_id !== s.membership.organization_id) ||
            (c.kind === 'publish' && v2.status !== 'published') ||
            (c.kind === 'withdraw' && v2.status !== 'withdrawn')
          )
            throw Error();
          return v2;
        },
        s.owner,
        n,
        mid,
        method,
        body,
        status,
      );
      if (!this.current(n, s.owner, mid)) return;
      if (c.kind === 'create') this.docId = (saved as Document).id;
      else if (c.kind !== 'index') this.versionId = (saved as Version).id;
      this.set({ index: null, phase: 'idle' });
      await this.load(false);
      if (this.getState().phase === 'ready') this.set({ notice: 'saved' });
    } catch (e) {
      this.fail(e, n, true);
    }
  }
  private fail(cause: unknown, n: number, mutation: boolean) {
    if (n !== this.serial) return;
    const e =
      cause instanceof ApiFailure
        ? cause
        : new ApiFailure('invalid-response', mutation ? 'unknown' : 'rejected');
    this.set({
      phase: mutation && e.outcome === 'unknown' ? 'unknown' : 'unavailable',
      index: null,
      page: null,
      document: null,
      versions: null,
      version: null,
      review: null,
      error: e,
      notice: null,
      retryAt: e.status === 429 ? Date.now() + (e.retryAfterMs ?? 1000) : 0,
    });
  }
}
