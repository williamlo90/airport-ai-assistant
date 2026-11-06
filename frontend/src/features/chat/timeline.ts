import type { AuthCoordinator } from '../../app/auth/coordinator';
import type { BoundarySnapshot } from '../../api/contracts';
import { ApiFailure } from '../../api/errors';
import {
  decodeEvents,
  type ConversationEvent,
  type EventPage,
} from './event-contracts';
export interface TimelineState {
  id: string | null;
  phase: 'idle' | 'loading' | 'ready' | 'paused' | 'error';
  items: readonly ConversationEvent[];
  snapshot: Omit<EventPage, 'items' | 'next_cursor' | 'has_more'> | null;
  hasMore: boolean;
  error: ApiFailure | null;
  retryAt: number;
}
const empty = (): TimelineState => ({
  id: null,
  phase: 'idle',
  items: [],
  snapshot: null,
  hasMore: false,
  error: null,
  retryAt: 0,
});
/** Ephemeral authorized projection. No disk storage, command replay or overlapping polls. */
export class ConversationTimeline {
  private state = empty();
  private listeners = new Set<() => void>();
  private serial = 0;
  private owner: BoundarySnapshot | null = null;
  private cursor: string | null = null;
  private position = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private controller: AbortController | null = null;
  private running = false;
  private polling = false;
  private errors = 0;
  private versionFloor = 1;
  private epochFloor = 0;
  private deadline = 0;
  constructor(
    readonly auth: AuthCoordinator,
    private onSnapshot: (
      snapshot: EventPage,
      signal: AbortSignal,
      current: () => boolean,
    ) => void | Promise<void>,
    private onDenied: (error: ApiFailure) => void = () => {},
  ) {}
  getState = () => this.state;
  subscribe = (f: () => void) => {
    this.listeners.add(f);
    return () => {
      this.listeners.delete(f);
    };
  };
  private set(v: Partial<TimelineState>) {
    this.state = { ...this.state, ...v };
    this.listeners.forEach((f) => f());
  }
  private cancel() {
    this.serial++;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.controller?.abort();
    this.controller = null;
    this.polling = false;
  }
  reset() {
    this.cancel();
    this.running = false;
    this.owner = null;
    this.cursor = null;
    this.position = 0;
    this.errors = 0;
    this.versionFloor = 1;
    this.epochFloor = 0;
    this.set({ ...empty(), retryAt: this.deadline });
  }
  start(id: string, version: number, epoch: number, enabled = true) {
    this.reset();
    this.running = enabled;
    this.owner = this.auth.capture();
    this.versionFloor = version;
    this.epochFloor = epoch;
    this.set({ id, phase: enabled ? 'loading' : 'paused' });
    if (enabled) void this.refresh(true);
  }
  pause() {
    this.cancel();
    this.running = false;
    this.cursor = null;
    this.position = 0;
    if (this.state.id)
      this.set({ phase: 'paused', items: [], snapshot: null, hasMore: false });
  }
  resume(version = this.versionFloor, epoch = this.epochFloor) {
    if (!this.state.id || !this.owner || !this.auth.isCurrent(this.owner))
      return;
    this.versionFloor = Math.max(version, this.versionFloor);
    this.epochFloor = Math.max(epoch, this.epochFloor);
    this.running = true;
    if (this.state.phase === 'paused') this.set({ phase: 'loading' });
    void this.refresh();
  }
  private schedule(ms: number) {
    if (!this.running) return;
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.refresh();
    }, ms);
  }
  reload() {
    if (!this.state.id || !this.owner || !this.auth.isCurrent(this.owner))
      return;
    this.cancel();
    this.running = true;
    this.cursor = null;
    this.position = 0;
    this.errors = 0;
    this.set({
      items: [],
      snapshot: null,
      hasMore: false,
      phase: 'loading',
      error: null,
    });
    void this.refresh(true);
  }
  canSend() {
    return (
      this.state.phase === 'ready' &&
      !this.state.hasMore &&
      this.state.snapshot?.mode !== 'ended'
    );
  }
  async refresh(restart = false) {
    if (!this.running || !this.owner || !this.state.id || this.polling) return;
    if (Date.now() < this.deadline) {
      this.set({ retryAt: this.deadline });
      this.schedule(this.deadline - Date.now());
      return;
    }
    if (restart) {
      this.cursor = null;
      this.position = 0;
      this.set({ items: [], snapshot: null, phase: 'loading', error: null });
    }
    const owner = this.owner,
      id = this.state.id,
      serial = ++this.serial,
      cursor = this.cursor,
      position = this.position;
    this.polling = true;
    this.controller = new AbortController();
    const current = () =>
      this.running &&
      serial === this.serial &&
      this.auth.isCurrent(owner) &&
      this.state.id === id;
    try {
      const params = new URLSearchParams({ limit: '50' });
      if (cursor) params.set('after', cursor);
      const page = await this.auth.request({
        method: 'GET',
        path: '/conversations/' + id + '/events?' + params,
        boundary: owner,
        signal: this.controller.signal,
        isRelevant: current,
        successStatus: 200,
        decode: (v) =>
          decodeEvents(v, position, this.versionFloor, this.epochFloor, cursor),
      });
      if (!current()) return;
      const all = [...this.state.items, ...page.items],
        ids = new Set<string>(),
        messages = new Set<string>();
      for (const e of all) {
        if (ids.has(e.id)) throw Error('Duplicate event id');
        ids.add(e.id);
        if ('message_id' in e.payload) {
          if (messages.has(e.payload.message_id))
            throw Error('Duplicate message id');
          messages.add(e.payload.message_id);
        }
      }
      // A newly observed head is not usable until language/basis metadata agrees.
      // Close the send/render gate before onSnapshot can publish newer metadata.
      if (
        this.state.snapshot &&
        (page.version !== this.state.snapshot.version ||
          page.generation_epoch !== this.state.snapshot.generation_epoch)
      )
        this.set({ phase: 'loading' });
      await this.onSnapshot(page, this.controller!.signal, current);
      if (!current()) return;
      this.cursor = page.next_cursor;
      this.position = page.items.at(-1)?.sequence ?? position;
      this.errors = 0;
      this.versionFloor = page.version;
      this.epochFloor = page.generation_epoch;
      this.set({
        phase: 'ready',
        items: all,
        snapshot: {
          mode: page.mode,
          generation_epoch: page.generation_epoch,
          version: page.version,
        },
        hasMore: page.has_more,
        error: null,
        retryAt: 0,
      });
      if (page.has_more) this.schedule(500);
      else if (page.mode !== 'ended')
        this.schedule(page.mode === 'ai' ? 10000 : 5000);
    } catch (e) {
      if (!current()) return;
      const error =
        e instanceof ApiFailure
          ? e
          : new ApiFailure('invalid-response', 'rejected');
      this.errors++;
      if (error.status === 429)
        this.deadline = Math.max(
          this.deadline,
          Date.now() + (error.retryAfterMs ?? 5000),
        );
      const denied =
        error.status === 401 ||
        error.status === 403 ||
        error.status === 404 ||
        error.code === 'SOURCE_UNAVAILABLE';
      this.set({
        phase: 'error',
        items: denied ? [] : this.state.items,
        snapshot: null,
        error,
        hasMore: false,
        retryAt: this.deadline,
      });
      if (denied) {
        this.cursor = null;
        this.position = 0;
        this.running = false;
        this.onDenied(error);
      } else if (
        this.errors <= 3 &&
        (error.status === 429 ||
          error.kind === 'network' ||
          error.kind === 'timeout' ||
          error.kind === 'stale' ||
          (error.status ?? 0) >= 500)
      )
        this.schedule(
          Math.max(5000 * 2 ** (this.errors - 1), this.deadline - Date.now()),
        );
    } finally {
      if (serial === this.serial) this.polling = false;
    }
  }
  dispose() {
    this.reset();
    this.listeners.clear();
  }
}
