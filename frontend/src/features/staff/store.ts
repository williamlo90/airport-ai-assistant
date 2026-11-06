import type { AuthCoordinator } from '../../app/auth/coordinator';
import type { Membership } from '../../app/auth/contracts';
import type { BoundarySnapshot } from '../../api/contracts';
import { ApiFailure } from '../../api/errors';
interface State {
  ready: boolean;
  principalId: string | null;
  memberships: readonly Membership[];
  selectedId: string | null;
  busy: boolean;
  operation: 'staff-login' | 'bootstrap' | 'logout' | null;
  message: 'rejected' | 'unknown' | 'prepared' | 'signedout' | null;
  error: ApiFailure | null;
  retryAt: number;
}
const empty = (): State => ({
  ready: false,
  principalId: null,
  memberships: [],
  selectedId: null,
  busy: false,
  operation: null,
  message: null,
  error: null,
  retryAt: 0,
});
export const canHandleCases = (m: Membership | null | undefined) =>
  !!m &&
  m.queue_ids.length > 0 &&
  m.roles.some((r) => r === 'cs' || r === 'supervisor');
/** Staff metadata comes only from the coordinator's fresh /staff/me inspection. */
export class StaffStore {
  private readWait = { queue: 0, detail: 0, action: 0 };
  readDeadline(channel: 'queue' | 'detail' | 'action') {
    return this.readWait[channel];
  }
  waitForReads(channel: 'queue' | 'detail' | 'action', until: number) {
    this.readWait[channel] = Math.max(this.readWait[channel], until);
  }
  private state = empty();
  private owner: BoundarySnapshot | null = null;
  private listeners = new Set<() => void>();
  private serial = 0;
  constructor(readonly auth: AuthCoordinator) {}
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
  private clearScope() {
    this.owner = null;
    this.set({
      ready: false,
      principalId: null,
      memberships: [],
      selectedId: null,
    });
  }
  private sync = () => {
    const state = this.auth.getState();
    if (state.phase !== 'ready' || state.kind !== 'staff') {
      this.clearScope();
      return;
    }
    try {
      const owner = this.auth.capture(),
        memberships = this.auth.membershipsFor(owner);
      const selected =
        this.owner && this.auth.isCurrent(this.owner)
          ? memberships.find((m) => m.id === this.state.selectedId)
          : null;
      this.owner = owner;
      this.set({
        ready: true,
        principalId: owner.principalId,
        memberships,
        selectedId:
          selected?.id ??
          (memberships.length === 1 ? memberships[0]!.id : null),
      });
    } catch {
      this.clearScope();
    }
  };
  connect() {
    const off = this.auth.subscribe(this.sync),
      reset = this.auth.onReset(() => this.clearScope());
    this.sync();
    return () => {
      off();
      reset();
    };
  }
  select(id: string) {
    if (
      !this.owner ||
      !this.auth.isCurrent(this.owner) ||
      !this.state.memberships.some((m) => m.id === id)
    )
      return;
    this.set({ selectedId: id });
  }
  captureScope() {
    if (!this.owner || !this.auth.isCurrent(this.owner)) return null;
    const membership = this.auth
      .membershipsFor(this.owner)
      .find((m) => m.id === this.state.selectedId);
    return membership ? { owner: this.owner, membership } : null;
  }
  async prepare() {
    if (this.auth.getState().phase !== 'guest') return;
    await this.run({ kind: 'bootstrap' });
  }
  async login(contact: string, password: string) {
    const state = this.auth.getState();
    if (state.phase !== 'ready' || state.kind !== 'anonymous') return;
    await this.run({ kind: 'staff-login', contact, password });
  }
  async logout(owner: BoundarySnapshot) {
    if (!this.auth.isCurrent(owner)) return;
    await this.run({ kind: 'logout' });
  }
  private async run(
    change: Extract<
      Parameters<AuthCoordinator['change']>[0],
      { kind: 'staff-login' | 'bootstrap' | 'logout' }
    >,
  ) {
    if (
      this.state.busy ||
      (change.kind !== 'logout' && Date.now() < this.state.retryAt)
    )
      return;
    const serial = ++this.serial;
    this.set({
      busy: true,
      operation: change.kind,
      message: null,
      error: null,
    });
    try {
      const result = await this.auth.change(change);
      if (this.serial !== serial) return;
      const retryAt =
        result.failure?.status === 429 && result.failure.retryAfterMs !== null
          ? Math.max(
              this.state.retryAt,
              Date.now() + result.failure.retryAfterMs,
            )
          : this.state.retryAt;
      this.set({
        retryAt,
        error: result.failure,
        message:
          result.outcome === 'unknown'
            ? 'unknown'
            : result.outcome === 'rejected'
              ? 'rejected'
              : change.kind === 'logout'
                ? 'signedout'
                : change.kind === 'bootstrap'
                  ? 'prepared'
                  : null,
      });
    } catch (e) {
      if (this.serial === serial)
        this.set({
          message: 'unknown',
          error:
            e instanceof ApiFailure ? e : new ApiFailure('stale', 'unknown'),
        });
    } finally {
      if (this.serial === serial) this.set({ busy: false });
    }
  }
  dispose() {
    this.serial++;
    this.owner = null;
    this.state = empty();
    this.listeners.clear();
  }
}
