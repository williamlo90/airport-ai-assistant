import type { StaffQueueStore } from './queue-store';
import { canHandleCases } from './store';
import { decodeCapabilities } from './queue-contracts';
import { ApiFailure } from '../../api/errors';
import {
  decodeDeliveries,
  decodeReplies,
  type DeliveryList,
  type EmailReply,
} from './mail-contracts';

interface Channel<T> {
  phase: 'idle' | 'loading' | 'ready' | 'error';
  data: T | null;
  error: ApiFailure | null;
}
const channel = <T>(): Channel<T> => ({
  phase: 'idle',
  data: null,
  error: null,
});
interface MailState {
  available: boolean;
  deliveries: Channel<DeliveryList>;
  replies: Channel<EmailReply[]>;
  denied: ApiFailure | null;
  retryAt: number;
}
const cooldown = new WeakMap<StaffQueueStore['staff'], number>();
const empty = (): MailState => ({
  available: false,
  deliveries: channel(),
  replies: channel(),
  denied: null,
  retryAt: 0,
});
/** Case/version/selected-membership projection. Only explicit GET refresh; no send/replay. */
export class StaffMailStore {
  private state = empty();
  private listeners = new Set<() => void>();
  private serial = 0;
  private scopeKey = '';
  private connected = false;
  private controllers = new Set<AbortController>();
  constructor(
    readonly queue: StaffQueueStore,
    readonly caseId: string,
  ) {}
  getState = () => this.state;
  subscribe = (fn: () => void) => {
    this.listeners.add(fn);
    return () => {
      this.listeners.delete(fn);
    };
  };
  private set(value: Partial<MailState>) {
    this.state = { ...this.state, ...value };
    this.listeners.forEach((fn) => fn());
  }
  private basis() {
    const q = this.queue.getState();
    let scope: ReturnType<StaffQueueStore['staff']['captureScope']>;
    try {
      scope = this.queue.staff.captureScope();
    } catch {
      return null;
    }
    const cap = q.capabilities,
      detail = q.detail;
    if (
      !scope ||
      !q.scopeKey ||
      !canHandleCases(scope.membership) ||
      q.phase !== 'ready' ||
      q.detailPhase !== 'ready' ||
      q.selectedId !== this.caseId ||
      detail?.id !== this.caseId ||
      cap?.case_id !== this.caseId ||
      cap.case_version !== detail.version ||
      cap.actor_principal_id !== scope.owner.principalId ||
      cap.membership_id !== scope.membership.id ||
      cap.membership_version !== scope.membership.version ||
      cap.organization.id !== scope.membership.organization_id ||
      !scope.membership.queue_ids.includes(cap.queue.id)
    )
      return null;
    const key = JSON.stringify([
      q.scopeKey,
      scope.owner.boundaryId,
      scope.owner.authEpoch,
      scope.owner.principalId,
      scope.membership.id,
      scope.membership.version,
      [...scope.membership.roles].sort(),
      [...scope.membership.queue_ids].sort(),
      this.caseId,
      detail.version,
      cap.organization.id,
      cap.queue.id,
    ]);
    return {
      ...scope,
      key,
      version: detail.version,
      organizationId: cap.organization.id,
      queueId: cap.queue.id,
    };
  }
  private cancel() {
    this.serial++;
    this.controllers.forEach((control) => control.abort());
    this.controllers.clear();
  }
  private sync = () => {
    const key = this.basis()?.key ?? '';
    if (key === this.scopeKey) return;
    this.cancel();
    this.scopeKey = key;
    this.set({
      ...empty(),
      available: !!key,
      retryAt: cooldown.get(this.queue.staff) ?? 0,
    });
  };
  connect() {
    this.connected = true;
    const queueOff = this.queue.subscribe(this.sync),
      source = this.queue.staff.auth.onSourceUnavailable(() => {
        this.cancel();
        this.scopeKey = '';
        this.set(empty());
      }),
      staffOff = this.queue.staff.subscribe(this.sync),
      reset = this.queue.staff.auth.onReset(() => {
        this.cancel();
        this.scopeKey = '';
        this.set(empty());
      });
    this.sync();
    return () => {
      this.connected = false;
      queueOff();
      staffOff();
      reset();
      source();
      this.cancel();
      this.scopeKey = '';
      this.set(empty());
    };
  }
  async load() {
    if (
      !this.connected ||
      this.state.deliveries.phase === 'loading' ||
      this.state.replies.phase === 'loading'
    )
      return;
    this.sync();
    const basis = this.basis();
    const retryAt = cooldown.get(this.queue.staff) ?? 0;
    if (!basis || Date.now() < retryAt || this.state.denied) return;
    const serial = ++this.serial;
    this.set({
      deliveries: { ...channel<DeliveryList>(), phase: 'loading' },
      replies: { ...channel<EmailReply[]>(), phase: 'loading' },
      retryAt: 0,
    });
    const current = () =>
      this.connected &&
      this.serial === serial &&
      this.basis()?.key === basis.key &&
      this.queue.staff.auth.isCurrent(basis.owner);
    const read = async <T>(
      kind: 'deliveries' | 'replies',
      decode: (value: unknown) => T,
      publish: (data: T) => void,
    ) => {
      const control = new AbortController();
      this.controllers.add(control);
      try {
        const data = await this.queue.staff.auth.readResource({
          path: `/staff/cases/${this.caseId}/email-${kind}`,
          method: 'GET',
          boundary: basis.owner,
          signal: control.signal,
          isRelevant: current,
          successStatus: 200,
          decode,
        });
        if (!current()) return;
        // Mail routes accept no membership selector. Recheck the selected
        // membership through its existing capability endpoint after each read,
        // before publishing any body or metadata to the view.
        const cap = await this.queue.staff.auth.readResource({
          path: `/staff/cases/${this.caseId}/capabilities?membership_id=${basis.membership.id}`,
          method: 'GET',
          boundary: basis.owner,
          signal: control.signal,
          isRelevant: current,
          successStatus: 200,
          decode: decodeCapabilities,
        });
        if (!current()) return;
        if (
          cap.case_id !== this.caseId ||
          cap.case_version !== basis.version ||
          cap.actor_principal_id !== basis.owner.principalId ||
          cap.membership_id !== basis.membership.id ||
          cap.membership_version !== basis.membership.version ||
          cap.organization.id !== basis.organizationId ||
          cap.queue.id !== basis.queueId
        )
          throw new ApiFailure('stale', 'rejected');
        publish(data);
      } catch (e) {
        if (!current()) return;
        const error =
          e instanceof ApiFailure
            ? e
            : new ApiFailure('invalid-response', 'rejected');
        if (
          error.status === 401 ||
          error.status === 403 ||
          error.status === 404 ||
          error.kind === 'stale'
        ) {
          this.cancel();
          this.set({
            deliveries: channel(),
            replies: channel(),
            denied: error,
          });
          return;
        }
        let wait = this.state.retryAt;
        if (error.status === 429) {
          wait = Math.max(wait, Date.now() + (error.retryAfterMs ?? 5000));
          cooldown.set(this.queue.staff, wait);
        }
        if (kind === 'deliveries')
          this.set({
            deliveries: { phase: 'error', data: null, error },
            retryAt: wait,
          });
        else
          this.set({
            replies: { phase: 'error', data: null, error },
            retryAt: wait,
          });
      } finally {
        this.controllers.delete(control);
      }
    };
    await Promise.allSettled([
      read('deliveries', decodeDeliveries, (data) =>
        this.set({ deliveries: { phase: 'ready', data, error: null } }),
      ),
      read('replies', decodeReplies, (data) =>
        this.set({ replies: { phase: 'ready', data, error: null } }),
      ),
    ]);
  }
}
