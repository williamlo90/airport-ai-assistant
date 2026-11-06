import type { AuthCoordinator } from '../../app/auth/coordinator';
import { uuid } from '../../api/contracts';
import { ApiFailure } from '../../api/errors';
import { decodeCitations, type CitationReceipt } from './citation-contracts';

interface CitationState {
  open: boolean;
  phase: 'idle' | 'loading' | 'ready' | 'error';
  receipt: CitationReceipt | null;
  error: ApiFailure | null;
  retryAt: number;
}
// Only a nonsensitive rate-limit deadline survives remount/auth transitions.
const cooldowns = new WeakMap<AuthCoordinator, number>();
export class CitationStore {
  private state: CitationState = {
    open: false,
    phase: 'idle',
    receipt: null,
    error: null,
    retryAt: 0,
  };
  private serial = 0;
  private controller: AbortController | null = null;
  private listeners = new Set<() => void>();
  private connected = false;
  constructor(
    private auth: AuthCoordinator,
    readonly conversationId: string,
    readonly messageId: string,
  ) {
    if (!uuid(conversationId) || !uuid(messageId))
      throw Error('Invalid citation resource');
  }
  getState = () => this.state;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  private set(value: Partial<CitationState>) {
    this.state = { ...this.state, ...value };
    this.listeners.forEach((listener) => listener());
  }
  connect() {
    this.connected = true;
    const off = this.auth.onReset(() => this.close());
    const offSource = this.auth.onSourceUnavailable(() => this.close());
    return () => {
      off();
      offSource();
      this.connected = false;
      this.close();
    };
  }
  close() {
    this.serial++;
    this.controller?.abort();
    this.controller = null;
    this.set({
      open: false,
      phase: 'idle',
      receipt: null,
      error: null,
      retryAt: cooldowns.get(this.auth) ?? 0,
    });
  }
  async load() {
    if (!this.connected || this.state.phase === 'loading') return;
    const deadline = cooldowns.get(this.auth) ?? 0;
    if (Date.now() < deadline) {
      this.set({
        open: true,
        phase: 'error',
        receipt: null,
        retryAt: deadline,
      });
      return;
    }
    const serial = ++this.serial;
    const control = new AbortController();
    this.controller = control;
    this.set({
      open: true,
      phase: 'loading',
      receipt: null,
      error: null,
      retryAt: 0,
    });
    try {
      const owner = this.auth.capture();
      if (owner.sessionKind === 'staff')
        throw new ApiFailure('stale', 'not-sent');
      const current = () =>
        this.connected && this.serial === serial && this.auth.isCurrent(owner);
      const value = await this.auth.readResource({
        method: 'GET',
        path: `/conversations/${this.conversationId}/messages/${this.messageId}/citations`,
        boundary: owner,
        signal: control.signal,
        isRelevant: current,
        successStatus: 200,
        decode: (v) => decodeCitations(v, this.messageId),
      });
      if (current()) this.set({ phase: 'ready', receipt: value });
    } catch (e) {
      if (!this.connected || this.serial !== serial) return;
      const error =
        e instanceof ApiFailure
          ? e
          : new ApiFailure('invalid-response', 'rejected');
      if (error.status === 403 || error.status === 404) {
        // The receipt is an owned conversation read. Recheck the conversation
        // through the existing auth reset/reload path, never retain revoked data.
        this.close();
        await this.auth.recheck();
        return;
      }
      const retryAt =
        error.status === 429 && error.retryAfterMs !== null
          ? Date.now() + error.retryAfterMs
          : 0;
      if (retryAt) cooldowns.set(this.auth, retryAt);
      this.set({ phase: 'error', receipt: null, error, retryAt });
    } finally {
      if (this.serial === serial) this.controller = null;
    }
  }
}
