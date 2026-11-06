import type { AuthCoordinator } from '../../app/auth/coordinator';
import { ApiFailure } from '../../api/errors';
import { feedback, ratingInput } from './contracts';
import type { Feedback, RatingInput, Target } from './contracts';
type Phase =
  'idle' | 'loading' | 'ready' | 'sending' | 'unknown' | 'unavailable';
interface State {
  phase: Phase;
  value: Feedback | null;
  error: ApiFailure | null;
  retryAt: number;
  notice: 'saved' | 'current' | null;
}
const empty = (): State => ({
  phase: 'idle',
  value: null,
  error: null,
  retryAt: 0,
  notice: null,
});
export class RatingStore {
  private state = empty();
  private serial = 0;
  private target: Target | null = null;
  private control: AbortController | null = null;
  private listeners = new Set<() => void>();
  constructor(readonly auth: AuthCoordinator) {}
  getState = () => this.state;
  subscribe = (fn: () => void) => {
    this.listeners.add(fn);
    return () => {
      this.listeners.delete(fn);
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
    this.target = null;
    this.set(empty());
  }
  connect() {
    const reset = this.auth.onReset(() => this.clear()),
      source = this.auth.onSourceUnavailable((e) => {
        const target = this.target;
        this.clear();
        this.target = target;
        this.set({ phase: 'unavailable', error: e });
      });
    return () => {
      reset();
      source();
      this.clear();
    };
  }
  async open(target: Target) {
    this.clear();
    this.target = target;
    await this.read(false);
  }
  async recover() {
    await this.read(true);
  }
  private async read(recover: boolean) {
    if (
      !this.target ||
      Date.now() < this.state.retryAt ||
      ['loading', 'sending'].includes(this.state.phase)
    )
      return;
    const target = this.target,
      serial = ++this.serial,
      control = new AbortController();
    this.control = control;
    this.set({ phase: 'loading', value: null, error: null, notice: null });
    try {
      const owner = this.auth.capture();
      if (owner.sessionKind === 'staff')
        throw new ApiFailure('invalid-request', 'not-sent');
      const result = await this.auth.readResource({
        path: this.path(target),
        method: 'GET',
        boundary: owner,
        signal: control.signal,
        isRelevant: () => this.serial === serial,
        successStatus: 200,
        decode: (v) => this.decode(v, target),
      });
      if (serial !== this.serial || !this.auth.isCurrent(owner)) return;
      this.set({
        phase: 'ready',
        value: result,
        retryAt: 0,
        notice: recover ? 'current' : null,
      });
    } catch (cause) {
      this.fail(cause, serial, false);
    }
  }
  private path(t: Target) {
    return (
      (t.kind === 'case' ? '/cases/' : '/conversations/') + t.id + '/ratings'
    );
  }
  private decode(v: unknown, target: Target) {
    const result = feedback.parse(v);
    if (result.target_id !== target.id || result.target_type !== target.kind)
      throw new Error('Mismatched feedback target');
    return result;
  }
  async save(score: number, comment: string) {
    if (
      !this.target ||
      this.state.phase !== 'ready' ||
      !this.state.value?.eligible ||
      Date.now() < this.state.retryAt
    )
      return;
    const body = ratingInput.safeParse({
      score,
      comment: comment || null,
      expected_version: this.state.value.rating?.version ?? 0,
    });
    if (!body.success) return;
    const target = this.target,
      serial = ++this.serial,
      control = new AbortController();
    this.control = control;
    this.set({ phase: 'sending', error: null, notice: null });
    try {
      const owner = this.auth.capture();
      const result = await this.auth.request({
        path: this.path(target),
        method: 'POST',
        body: body.data,
        boundary: owner,
        signal: control.signal,
        isRelevant: () => this.serial === serial,
        successStatus: 200,
        decode: (v) => this.decode(v, target),
      });
      if (serial !== this.serial || !this.auth.isCurrent(owner)) return;
      this.assertSaved(result, body.data);
      this.set({ phase: 'ready', value: result, notice: 'saved' });
    } catch (cause) {
      this.fail(cause, serial, true);
    }
  }
  private assertSaved(v: Feedback, b: RatingInput) {
    if (
      !v.rating ||
      v.rating.score !== b.score ||
      v.rating.comment !== b.comment ||
      ![b.expected_version, b.expected_version + 1].includes(v.rating.version)
    )
      throw new ApiFailure('invalid-response', 'unknown');
  }
  private fail(cause: unknown, serial: number, mutation: boolean) {
    if (serial !== this.serial) return;
    const e =
      cause instanceof ApiFailure
        ? cause
        : new ApiFailure('invalid-response', mutation ? 'unknown' : 'rejected');
    this.set({
      phase: mutation && e.outcome === 'unknown' ? 'unknown' : 'unavailable',
      value: null,
      error: e,
      notice: null,
      retryAt: e.status === 429 ? Date.now() + (e.retryAfterMs ?? 1000) : 0,
    });
  }
}
