import type { AuthCoordinator } from '../../app/auth/coordinator';
import { ApiFailure } from '../../api/errors';
import { decodeCustomerCases } from './customer-contracts';
import type {
  CustomerCasePage,
  CustomerCaseStatus,
} from './customer-contracts';

interface State {
  phase: 'idle' | 'loading' | 'ready' | 'unavailable';
  page: CustomerCasePage | null;
  status: CustomerCaseStatus | null;
  error: ApiFailure | null;
  canGoBack: boolean;
  retryAt: number;
}
const empty = (): State => ({
  phase: 'idle',
  page: null,
  status: null,
  error: null,
  canGoBack: false,
  retryAt: 0,
});
/** One visible live page; no private list, cursor or query is persisted to browser storage. */
export class CustomerCasesStore {
  private state = empty();
  private serial = 0;
  private control: AbortController | null = null;
  private positions: (string | null)[] = [null];
  private listeners = new Set<() => void>();
  constructor(readonly auth: AuthCoordinator) {}
  getState = () => this.state;
  subscribe = (fn: () => void) => {
    this.listeners.add(fn);
    return () => {
      this.listeners.delete(fn);
    };
  };
  private set(value: Partial<State>) {
    this.state = { ...this.state, ...value };
    this.listeners.forEach((fn) => fn());
  }
  clear() {
    this.serial++;
    this.control?.abort();
    this.control = null;
    this.positions = [null];
    this.set(empty());
  }
  connect() {
    const off = this.auth.onReset(() => this.clear());
    const source = this.auth.onSourceUnavailable((error) => {
      this.clear();
      this.set({ phase: 'unavailable', error });
    });
    return () => {
      off();
      source();
      this.clear();
    };
  }
  async refresh(status: CustomerCaseStatus | null = this.state.status) {
    if (Date.now() < this.state.retryAt) return;
    this.positions = [null];
    await this.load(status);
  }
  async next() {
    if (this.state.phase !== 'ready' || !this.state.page?.next_cursor) return;
    this.positions.push(this.state.page.next_cursor);
    await this.load(this.state.status);
  }
  async previous() {
    if (this.state.phase !== 'ready' || this.positions.length <= 1) return;
    this.positions.pop();
    await this.load(this.state.status);
  }
  private async load(status: CustomerCaseStatus | null) {
    this.control?.abort();
    const serial = ++this.serial,
      control = new AbortController();
    this.control = control;
    this.set({
      phase: 'loading',
      page: null,
      status,
      error: null,
      canGoBack: false,
    });
    try {
      const owner = this.auth.capture();
      if (owner.sessionKind !== 'customer')
        throw new ApiFailure('invalid-request', 'not-sent');
      const cursor = this.positions.at(-1),
        query = new URLSearchParams({ limit: '25' });
      if (status) query.set('status', status);
      if (cursor) query.set('cursor', cursor);
      const page = await this.auth.readResource({
        path: '/cases?' + query,
        method: 'GET',
        boundary: owner,
        signal: control.signal,
        isRelevant: () => this.serial === serial,
        successStatus: 200,
        decode: decodeCustomerCases,
      });
      if (this.serial !== serial || !this.auth.isCurrent(owner)) return;
      if (page.status !== status)
        throw new ApiFailure('invalid-response', 'rejected');
      this.set({
        phase: 'ready',
        page,
        canGoBack: this.positions.length > 1,
        retryAt: 0,
      });
    } catch (cause) {
      if (serial !== this.serial) return;
      const error =
        cause instanceof ApiFailure
          ? cause
          : new ApiFailure('invalid-response', 'unknown');
      this.positions = [null];
      this.set({
        phase: 'unavailable',
        page: null,
        error,
        canGoBack: false,
        retryAt:
          error.status === 429 ? Date.now() + (error.retryAfterMs ?? 1000) : 0,
      });
    }
  }
}
