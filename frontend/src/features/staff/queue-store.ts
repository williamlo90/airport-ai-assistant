import { QueryClient } from '@tanstack/react-query';
import type { BoundarySnapshot, Decoder } from '../../api/contracts';
import { uuid } from '../../api/contracts';
import { ApiFailure } from '../../api/errors';
import type { Membership } from '../../app/auth/contracts';
import { canHandleCases, type StaffStore } from './store';
import {
  defaultFilter,
  decodeQueue,
  decodeCase,
  decodeCapabilities,
} from './queue-contracts';
import type { QueueFilter, QueuePage, Capabilities } from './queue-contracts';
import type { CaseDetail } from '../cases/detail-contracts';
type Phase = 'idle' | 'loading' | 'ready' | 'unavailable';
interface State {
  scopeKey: string;
  filter: QueueFilter;
  phase: Phase;
  page: QueuePage | null;
  pageNumber: number;
  error: ApiFailure | null;
  selectedId: string | null;
  detailPhase: Phase;
  detail: CaseDetail | null;
  capabilities: Capabilities | null;
  detailError: ApiFailure | null;
  retryAt: number;
}
const empty = (): State => ({
  scopeKey: '',
  filter: defaultFilter(),
  phase: 'idle',
  page: null,
  pageNumber: 1,
  error: null,
  selectedId: null,
  detailPhase: 'idle',
  detail: null,
  capabilities: null,
  detailError: null,
  retryAt: 0,
});
const fail = (e: unknown) =>
  e instanceof ApiFailure ? e : new ApiFailure('invalid-response', 'rejected');
export class StaffQueueStore {
  private state = empty();
  private listeners = new Set<() => void>();
  private owner: BoundarySnapshot | null = null;
  private member: Membership | null = null;
  private queueSerial = 0;
  private detailSerial = 0;
  private controllers = new Map<AbortController, 'queue' | 'detail'>();
  private cursors: (string | null)[] = [null];
  private flight: Promise<void> | null = null;
  private queueRetryAt = 0;
  private detailRetryAt = 0;
  scrollTop = 0;
  windowScrollY = 0;
  rememberListScroll(value: number) {
    this.scrollTop = value;
  }
  rememberWindowScroll(value: number) {
    this.windowScrollY = value;
  }
  readonly queries = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        staleTime: 0,
        gcTime: 0,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
      },
      mutations: { retry: false },
    },
  });
  constructor(
    readonly staff: StaffStore,
    private deployment: string,
  ) {
    this.queueRetryAt = staff.readDeadline('queue');
    this.detailRetryAt = staff.readDeadline('detail');
  }
  getState = () => this.state;
  subscribe = (f: () => void) => {
    this.listeners.add(f);
    return () => {
      this.listeners.delete(f);
    };
  };
  private set(v: Partial<State>) {
    this.state = {
      ...this.state,
      ...v,
      retryAt: Math.max(this.queueRetryAt, this.detailRetryAt),
    };
    this.listeners.forEach((f) => f());
  }
  private abort() {
    this.controllers.forEach((_, c) => c.abort());
    this.controllers.clear();
    void this.queries.cancelQueries();
    this.queries.clear();
  }
  private cancelDetail() {
    for (const [controller, channel] of this.controllers) {
      if (channel === 'detail') {
        controller.abort();
        this.controllers.delete(controller);
      }
    }
  }
  private clear() {
    this.queueSerial++;
    this.detailSerial++;
    this.flight = null;
    this.abort();
    this.owner = null;
    this.member = null;
    this.cursors = [null];
    this.scrollTop = 0;
    this.windowScrollY = 0;
    this.set(empty());
  }
  private sync = () => {
    let scope: ReturnType<StaffStore['captureScope']>;
    try {
      scope = this.staff.captureScope();
    } catch {
      scope = null;
    }
    const key =
      scope && canHandleCases(scope.membership)
        ? JSON.stringify([
            scope.owner.boundaryId,
            scope.owner.authEpoch,
            scope.owner.principalId,
            scope.membership.id,
            scope.membership.version,
            [...scope.membership.roles].sort(),
            [...scope.membership.queue_ids].sort(),
          ])
        : '';
    if (key === this.state.scopeKey) return;
    this.clear();
    if (key && scope) {
      this.owner = scope.owner;
      this.member = scope.membership;
      this.set({ scopeKey: key });
    }
  };
  connect() {
    const off = this.staff.subscribe(this.sync),
      source = this.staff.auth.onSourceUnavailable(() => this.clear()),
      reset = this.staff.auth.onReset(() => this.clear());
    this.sync();
    return () => {
      off();
      reset();
      source();
    };
  }
  private current(owner: BoundarySnapshot, key: string) {
    return (
      this.owner === owner &&
      this.state.scopeKey === key &&
      this.staff.auth.isCurrent(owner)
    );
  }
  private async read<T>(
    path: string,
    owner: BoundarySnapshot,
    key: string,
    serial: number,
    channel: 'queue' | 'detail',
    decode: Decoder<T>,
  ) {
    const controller = new AbortController();
    this.controllers.set(controller, channel);
    const relevant = () =>
      this.current(owner, key) &&
      (channel === 'queue' ? this.queueSerial : this.detailSerial) === serial;
    try {
      return await this.queries.fetchQuery({
        queryKey: [
          this.deployment,
          owner.boundaryId,
          owner.authEpoch,
          owner.principalId,
          key,
          channel,
          path,
          serial,
        ],
        queryFn: () =>
          this.staff.auth.readResource({
            path,
            method: 'GET',
            boundary: owner,
            isRelevant: relevant,
            signal: controller.signal,
            successStatus: 200,
            decode,
          }),
      });
    } finally {
      this.controllers.delete(controller);
    }
  }
  configure(filter: Partial<QueueFilter>) {
    this.sync();
    const next = { ...this.state.filter, ...filter };
    if (JSON.stringify(next) === JSON.stringify(this.state.filter)) return;
    this.queueSerial++;
    this.detailSerial++;
    this.flight = null;
    this.abort();
    this.cursors = [null];
    this.scrollTop = 0;
    this.windowScrollY = 0;
    this.set({
      filter: next,
      page: null,
      phase: 'idle',
      pageNumber: 1,
      error: null,
      selectedId: null,
      detail: null,
      capabilities: null,
      detailPhase: 'idle',
      detailError: null,
    });
  }
  ensureQueue() {
    if (this.state.phase === 'ready') return Promise.resolve();
    return this.loadQueue();
  }
  loadQueue(refresh = false): Promise<void> {
    this.sync();
    if (this.flight) return this.flight;
    if (!this.owner || !this.member || Date.now() < this.queueRetryAt)
      return Promise.resolve();
    if (refresh) {
      this.cursors = [null];
      this.scrollTop = 0;
      this.windowScrollY = 0;
      this.set({ pageNumber: 1 });
    }
    const owner = this.owner,
      member = this.member,
      key = this.state.scopeKey,
      filter = { ...this.state.filter },
      serial = ++this.queueSerial;
    const cursor = this.cursors[this.cursors.length - 1] ?? null,
      watermark = cursor ? this.state.page?.watermark : undefined;
    if (this.state.page) {
      this.cancelDetail();
      this.detailSerial++;
      this.set({
        detail: null,
        capabilities: null,
        detailPhase: 'idle',
        detailError: null,
      });
    }
    this.set({ phase: 'loading', page: null, error: null });
    const query = new URLSearchParams({
      membership_id: member.id,
      view: filter.view,
      sort: 'created_asc',
      limit: '25',
    });
    if (filter.queueId) query.set('queue_id', filter.queueId);
    if (filter.status) query.set('status', filter.status);
    if (cursor) query.set('cursor', cursor);
    const work = (async () => {
      try {
        const page = await this.read(
          '/staff/queues/cases?' + query,
          owner,
          key,
          serial,
          'queue',
          (v) => decodeQueue(v, member, filter, watermark, cursor),
        );
        if (this.current(owner, key) && serial === this.queueSerial)
          this.set({ page, phase: 'ready', pageNumber: this.cursors.length });
      } catch (e) {
        if (!this.current(owner, key) || serial !== this.queueSerial) return;
        const error = fail(e);
        if (error.status === 429) {
          this.queueRetryAt = Math.max(
            this.queueRetryAt,
            Date.now() + (error.retryAfterMs ?? 0),
          );
          this.staff.waitForReads('queue', this.queueRetryAt);
        }
        this.detailSerial++;
        this.set({
          phase: 'unavailable',
          page: null,
          error,
          detail: null,
          capabilities: null,
          detailPhase: 'idle',
        });
        this.queries.clear();
        if (error.status === 403 || error.status === 404) {
          this.cursors = [null];
          this.scrollTop = 0;
          this.windowScrollY = 0;
          this.set({ selectedId: null });
        }
      } finally {
        if (serial === this.queueSerial) this.flight = null;
      }
    })();
    this.flight = work;
    return work;
  }
  async next() {
    const page = this.state.page;
    if (
      this.state.phase !== 'ready' ||
      !page?.next_cursor ||
      Date.now() < this.queueRetryAt
    )
      return;
    this.cursors.push(page.next_cursor);
    this.scrollTop = 0;
    this.windowScrollY = 0;
    await this.loadQueue();
  }
  async previous() {
    if (
      this.cursors.length < 2 ||
      this.state.phase === 'loading' ||
      Date.now() < this.queueRetryAt
    )
      return;
    this.cursors.pop();
    this.scrollTop = 0;
    this.windowScrollY = 0;
    await this.loadQueue();
  }
  clearDetail() {
    if (this.state.selectedId === null) return;
    this.cancelDetail();
    this.detailSerial++;
    this.set({
      selectedId: null,
      detail: null,
      capabilities: null,
      detailPhase: 'idle',
      detailError: null,
    });
  }
  async openCase(id: string, refresh = false) {
    this.sync();
    id = id.toLowerCase();
    if (!uuid(id)) return;
    if (
      !refresh &&
      this.state.selectedId === id &&
      (this.state.detailPhase === 'ready' ||
        this.state.detailPhase === 'loading')
    )
      return;
    this.cancelDetail();
    const serial = ++this.detailSerial;
    this.set({
      selectedId: id,
      detail: null,
      capabilities: null,
      detailPhase: 'loading',
      detailError: null,
    });
    await this.ensureQueue();
    if (serial !== this.detailSerial || this.state.selectedId !== id) return;
    if (
      !this.owner ||
      !this.member ||
      !this.state.page ||
      Date.now() < this.detailRetryAt
    ) {
      this.set({ detailPhase: 'unavailable' });
      return;
    }
    const owner = this.owner,
      member = this.member,
      key = this.state.scopeKey,
      page = this.state.page;
    try {
      const suffix = '?membership_id=' + member.id;
      const [detail, capabilities] = await Promise.all([
        this.read(
          '/staff/cases/' + id + suffix,
          owner,
          key,
          serial,
          'detail',
          decodeCase,
        ),
        this.read(
          '/staff/cases/' + id + '/capabilities' + suffix,
          owner,
          key,
          serial,
          'detail',
          decodeCapabilities,
        ),
      ]);
      if (
        !this.current(owner, key) ||
        serial !== this.detailSerial ||
        this.state.selectedId !== id
      )
        return;
      if (
        detail.id !== id ||
        capabilities.case_id !== id ||
        capabilities.case_version !== detail.version ||
        capabilities.actor_principal_id !== owner.principalId ||
        capabilities.membership_id !== member.id ||
        capabilities.membership_version !== member.version ||
        capabilities.organization.id !== member.organization_id ||
        !page.queues.some((q) => q.id === capabilities.queue.id) ||
        (capabilities.assignee &&
          capabilities.assignee.membership_id !== detail.assigned_membership_id)
      )
        throw new ApiFailure('invalid-response', 'rejected');
      this.set({ detail, capabilities, detailPhase: 'ready' });
    } catch (e) {
      if (!this.current(owner, key) || serial !== this.detailSerial) return;
      const error = fail(e);
      if (error.status === 403 || error.status === 404)
        this.staff.auth.forgetRetained(
          '/staff/cases/' + id + '?membership_id=' + member.id,
          owner,
        );
      if (error.status === 429) {
        this.detailRetryAt = Math.max(
          this.detailRetryAt,
          Date.now() + (error.retryAfterMs ?? 0),
        );
        this.staff.waitForReads('detail', this.detailRetryAt);
      }
      const page = this.state.page;
      this.queries.clear();
      this.set({
        detail: null,
        capabilities: null,
        detailPhase: 'unavailable',
        detailError: error,
        ...((error.status === 403 || error.status === 404) && page
          ? { page: { ...page, items: page.items.filter((v) => v.id !== id) } }
          : {}),
      });
    }
  }
  dispose() {
    this.clear();
    this.listeners.clear();
  }
}
