import type { StaffStore } from '../staff/store';
import type { BoundarySnapshot } from '../../api/contracts';
import { ApiFailure } from '../../api/errors';
import { accessEdit, directory, result } from './contracts';
import type { AccessEdit, Directory, MemberResult, Member } from './contracts';
export interface Review {
  before: Member;
  after: AccessEdit;
  actorVersion: number;
}
interface State {
  phase:
    | 'idle'
    | 'loading'
    | 'ready'
    | 'review'
    | 'sending'
    | 'unknown'
    | 'unavailable';
  page: Directory | null;
  current: MemberResult | null;
  edit: AccessEdit | null;
  review: Review | null;
  error: ApiFailure | null;
  notice: 'saved' | 'current' | null;
  retryAt: number;
  canBack: boolean;
}
const empty = (): State => ({
  phase: 'idle',
  page: null,
  current: null,
  edit: null,
  review: null,
  error: null,
  notice: null,
  retryAt: 0,
  canBack: false,
});
export class AdminStore {
  private state = empty();
  private serial = 0;
  private control: AbortController | null = null;
  private scopeId: string | null = null;
  private targetId: string | null = null;
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
    this.control = null;
    this.targetId = null;
    this.positions = [null];
    this.set(empty());
  }
  private scope() {
    const s = this.staff.captureScope();
    if (!s || !s.membership.roles.includes('admin'))
      throw new ApiFailure('invalid-request', 'not-sent');
    return s;
  }
  connect() {
    const sync = () => {
      let next: null | string = null;
      try {
        const scope = this.scope();
        next = JSON.stringify([
          scope.owner.authEpoch,
          scope.owner.principalId,
          scope.membership,
        ]);
      } catch {
        /* no access */
      }
      if (next !== this.scopeId) {
        this.clear();
        this.scopeId = next;
      }
    };
    const off = this.staff.subscribe(sync),
      reset = this.staff.auth.onReset(() => {
        this.clear();
        this.scopeId = null;
      }),
      source = this.staff.auth.onSourceUnavailable((e) => {
        this.clear();
        this.set({ phase: 'unavailable', error: e });
      });
    sync();
    return () => {
      off();
      reset();
      source();
      this.clear();
    };
  }
  async refresh() {
    this.targetId = null;
    this.positions = [null];
    await this.load(false);
  }
  async next() {
    if (this.state.phase !== 'ready' || !this.state.page?.next_after) return;
    this.positions.push(this.state.page.next_after);
    this.targetId = null;
    await this.load(false);
  }
  async previous() {
    if (this.state.phase !== 'ready' || this.positions.length <= 1) return;
    this.positions.pop();
    this.targetId = null;
    await this.load(false);
  }
  async choose(id: string) {
    if (
      this.state.phase !== 'ready' ||
      !this.state.page?.items.some((m) => m.principal_id === id)
    )
      return;
    this.targetId = id;
    await this.load(false);
  }
  async recover() {
    await this.load(true);
  }
  private async load(recovery: boolean) {
    if (
      ['loading', 'sending'].includes(this.state.phase) ||
      Date.now() < this.state.retryAt
    )
      return;
    this.control?.abort();
    const serial = ++this.serial,
      control = new AbortController();
    this.control = control;
    this.set({
      phase: 'loading',
      page: null,
      current: null,
      edit: null,
      review: null,
      error: null,
      notice: null,
    });
    try {
      const s = this.scope(),
        actor = s.membership.id,
        target = this.targetId;
      const query = new URLSearchParams({ membership_id: actor, limit: '25' });
      const after = this.positions.at(-1);
      if (after) query.set('after', after);
      const data = target
        ? await this.staff.auth.readResource({
            path:
              '/admin/staff/' + target + '/memberships?membership_id=' + actor,
            method: 'GET',
            boundary: s.owner,
            signal: control.signal,
            isRelevant: () => this.relevant(serial, s.owner, actor),
            successStatus: 200,
            decode: (v) =>
              this.checkMember(v, actor, target, s.membership.organization_id),
          })
        : await this.staff.auth.readResource({
            path: '/admin/staff?' + query,
            method: 'GET',
            boundary: s.owner,
            signal: control.signal,
            isRelevant: () => this.relevant(serial, s.owner, actor),
            successStatus: 200,
            decode: (v) => {
              const d = directory.parse(v);
              if (
                d.workspace.membership_id !== actor ||
                d.workspace.organization.id !== s.membership.organization_id ||
                d.items.some(
                  (m) =>
                    after !== null &&
                    after !== undefined &&
                    m.principal_id <= after,
                ) ||
                d.items.some(
                  (m, i) =>
                    i > 0 && d.items[i - 1]!.principal_id >= m.principal_id,
                ) ||
                (d.has_more &&
                  (d.items.length !== 25 ||
                    d.next_after !== d.items.at(-1)?.principal_id))
              )
                throw new Error('Directory scope');
              return d;
            },
          });
      if (!this.relevant(serial, s.owner, actor)) return;
      if ('member' in data)
        this.set({
          phase: 'ready',
          current: data,
          edit: this.editOf(data.member),
          notice: recovery ? 'current' : null,
          retryAt: 0,
        });
      else
        this.set({
          phase: 'ready',
          page: data,
          canBack: this.positions.length > 1,
          retryAt: 0,
        });
    } catch (e) {
      this.fail(e, serial, false);
    }
  }
  private relevant(serial: number, owner: BoundarySnapshot, actor: string) {
    try {
      return (
        this.serial === serial &&
        this.staff.auth.isCurrent(owner) &&
        this.scope().membership.id === actor
      );
    } catch {
      return false;
    }
  }
  private checkMember(v: unknown, actor: string, target: string, org: string) {
    const d = result.parse(v);
    if (
      d.workspace.membership_id !== actor ||
      d.workspace.organization.id !== org ||
      d.member.principal_id !== target ||
      d.member.organization_id !== org
    )
      throw new Error('Membership scope');
    return d;
  }
  private editOf(m: Member): AccessEdit {
    return {
      active: m.active,
      roles: [...m.roles],
      queue_ids: [...m.queue_ids],
    };
  }
  edit(v: AccessEdit) {
    if (
      !['ready', 'review'].includes(this.state.phase) ||
      !this.state.current ||
      this.isSelf
    )
      return;
    this.set({ phase: 'ready', edit: v, review: null, notice: null });
  }
  get isSelf() {
    return (
      this.state.current?.member.principal_id ===
      this.staff.getState().principalId
    );
  }
  review() {
    const { edit, current } = this.state;
    if (this.state.phase !== 'ready' || !edit || !current || this.isSelf)
      return;
    const parsed = accessEdit.safeParse(edit);
    if (
      !parsed.success ||
      edit.queue_ids.some(
        (id) => !current.workspace.queues.some((q) => q.id === id && q.active),
      )
    )
      return;
    this.set({
      phase: 'review',
      review: {
        before: current.member,
        after: structuredClone(parsed.data),
        actorVersion: current.workspace.membership_version,
      },
    });
  }
  cancel() {
    if (this.state.phase === 'review')
      this.set({ phase: 'ready', review: null });
  }
  discard() {
    if (['sending', 'unknown', 'loading'].includes(this.state.phase)) return;
    const c = this.state.current;
    this.set({
      phase: 'ready',
      edit: c ? this.editOf(c.member) : null,
      review: null,
    });
  }
  async save() {
    const r = this.state.review,
      c = this.state.current;
    if (
      this.state.phase !== 'review' ||
      !r ||
      !c ||
      this.isSelf ||
      Date.now() < this.state.retryAt
    )
      return;
    const serial = ++this.serial,
      control = new AbortController();
    this.control = control;
    this.set({
      phase: 'sending',
      error: null,
      notice: null,
      review: null,
      edit: null,
    });
    try {
      const s = this.scope(),
        actor = s.membership.id,
        target = c.member.principal_id;
      const data = await this.staff.auth.request({
        path: '/admin/staff/' + target + '/memberships',
        method: 'PATCH',
        boundary: s.owner,
        signal: control.signal,
        isRelevant: () => this.relevant(serial, s.owner, actor),
        successStatus: 200,
        body: {
          actor_membership_id: actor,
          actor_version: r.actorVersion,
          expected_version: r.before.version,
          ...r.after,
        },
        decode: (v) =>
          this.checkMember(v, actor, target, s.membership.organization_id),
      });
      if (!this.relevant(serial, s.owner, actor)) return;
      if (
        data.member.active !== r.after.active ||
        JSON.stringify([...data.member.roles].sort()) !==
          JSON.stringify([...r.after.roles].sort()) ||
        JSON.stringify([...data.member.queue_ids].sort()) !==
          JSON.stringify([...r.after.queue_ids].sort()) ||
        ![r.before.version, r.before.version + 1].includes(data.member.version)
      )
        throw new ApiFailure('invalid-response', 'unknown');
      this.set({
        phase: 'ready',
        current: data,
        edit: this.editOf(data.member),
        notice: 'saved',
      });
    } catch (e) {
      this.fail(e, serial, true);
    }
  }
  private fail(cause: unknown, serial: number, mutation: boolean) {
    if (serial !== this.serial) return;
    const error =
      cause instanceof ApiFailure
        ? cause
        : new ApiFailure('invalid-response', mutation ? 'unknown' : 'rejected');
    this.set({
      phase:
        mutation && error.outcome === 'unknown' ? 'unknown' : 'unavailable',
      page: null,
      current: null,
      edit: null,
      review: null,
      error,
      notice: null,
      retryAt:
        error.status === 429 ? Date.now() + (error.retryAfterMs ?? 1000) : 0,
    });
  }
}
