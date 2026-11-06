import type { AuthCoordinator } from '../../app/auth/coordinator';
import type { BoundarySnapshot } from '../../api/contracts';
import { ApiFailure } from '../../api/errors';
import { agentApi } from './agent-api';
import { privateReferences, type PrivateReferences } from './agent-contracts';
import type {
  AgentOutcome,
  PublicService,
  StartAnswer,
} from './agent-contracts';

export interface AgentBasis {
  conversationId: string;
  messageId: string;
  version: number;
  epoch: number;
  language: 'en' | 'id';
}
export interface AgentPort {
  basis(): AgentBasis | null;
  compatible(basis: AgentBasis): boolean;
  hold(): () => void;
  refresh(): void;
  revoke?(error: ApiFailure): void;
}
type Phase =
  | 'idle'
  | 'starting'
  | 'answering'
  | 'prepared'
  | 'unknown-start'
  | 'unknown-answer'
  | 'unavailable'
  | 'published'
  | 'discarded'
  | 'review'
  | 'draft';
interface Attempt {
  owner: BoundarySnapshot;
  basis: AgentBasis;
  command: StartAnswer;
  runId: string | null;
  phase: Phase;
  outcome: AgentOutcome | null;
  error: ApiFailure | null;
}
export interface AgentState {
  conversationId: string | null;
  phase: Phase;
  outcome: AgentOutcome | null;
  error: ApiFailure | null;
  retryAt: number;
  privateAvailable: boolean;
}
const empty = (): AgentState => ({
  conversationId: null,
  phase: 'idle',
  outcome: null,
  error: null,
  retryAt: 0,
  privateAvailable: false,
});
const sameBasis = (a: AgentBasis, b: AgentBasis) =>
  a.conversationId === b.conversationId &&
  a.messageId === b.messageId &&
  a.version === b.version &&
  a.epoch === b.epoch &&
  a.language === b.language;

/** Owner-bound memory ledger. No persisted command, model replay or inferred receipt. */
export class AgentStore {
  private state = empty();
  private listeners = new Set<() => void>();
  private attempts = new Map<string, Attempt>();
  // Non-sensitive tombstones prevent erasing a command from enabling a second
  // model execution for the same customer message after revocation/visibility.
  private forgotten = new Set<string>();
  private active: { id: string; token: string } | null = null;
  private serial = 0;
  private controller: AbortController | null = null;
  private capabilityController: AbortController | null = null;
  private release: (() => void) | null = null;
  private deadline = 0;
  constructor(
    private auth: AuthCoordinator,
    private port: AgentPort,
  ) {}
  getState = () => this.state;
  subscribe = (fn: () => void) => {
    this.listeners.add(fn);
    return () => {
      this.listeners.delete(fn);
    };
  };
  private set(value: Partial<AgentState>) {
    this.state = { ...this.state, ...value };
    this.listeners.forEach((fn) => fn());
  }
  private show(attempt: Attempt) {
    this.set({
      conversationId: attempt.basis.conversationId,
      phase: attempt.phase,
      outcome: attempt.outcome,
      error: attempt.error,
      retryAt: this.deadline,
    });
  }
  get busy() {
    return this.state.phase === 'starting' || this.state.phase === 'answering';
  }
  connect() {
    const off = this.auth.onReset(() => this.reset());
    const offSource = this.auth.onSourceUnavailable(() => {
      for (const id of [...this.attempts.keys()]) this.forget(id);
    });
    return () => {
      off();
      offSource();
    };
  }
  attach(id: string, token: string) {
    this.detach();
    this.active = { id, token };
    const old = this.attempts.get(id);
    if (old && this.auth.isCurrent(old.owner)) this.show(old);
    else this.set({ ...empty(), conversationId: id, retryAt: this.deadline });
    void this.readCapabilities(id, token);
    return () => {
      if (this.active?.token === token) this.detach();
    };
  }
  private async readCapabilities(id: string, token: string) {
    if (this.auth.getState().phase !== 'ready') return;
    let owner: BoundarySnapshot;
    try {
      owner = this.auth.capture();
    } catch {
      return;
    }
    if (owner.sessionKind !== 'customer') return;
    const current = () =>
      this.active?.id === id &&
      this.active.token === token &&
      this.auth.isCurrent(owner);
    const control = new AbortController();
    this.capabilityController?.abort();
    this.capabilityController = control;
    try {
      const result = await agentApi(this.auth).capabilities({
        boundary: owner,
        signal: control.signal,
        isRelevant: current,
      });
      if (current()) this.set({ privateAvailable: result.private_assistance });
    } catch {
      /* Unknown capability remains disabled; no retries. */
    } finally {
      if (this.capabilityController === control)
        this.capabilityController = null;
    }
  }
  private cancel() {
    this.serial++;
    this.controller?.abort();
    this.controller = null;
    const release = this.release;
    this.release = null;
    release?.();
  }
  detach() {
    this.capabilityController?.abort();
    this.capabilityController = null;
    const old = this.active && this.attempts.get(this.active.id);
    if (old?.command.purpose === 'assist')
      this.forget(old.basis.conversationId);
    if (old?.phase === 'starting') old.phase = 'unknown-start';
    if (old?.phase === 'answering') old.phase = 'unknown-answer';
    this.active = null;
    this.cancel();
    this.set(empty());
  }
  reset() {
    this.attempts.clear();
    this.forgotten.clear();
    this.detach();
  }
  forget(id: string) {
    const old = this.attempts.get(id);
    if (old) this.forgotten.add(old.basis.messageId);
    this.attempts.delete(id);
    if (this.active?.id === id) {
      this.cancel();
      this.set({
        ...empty(),
        conversationId: id,
        privateAvailable: this.state.privateAvailable,
      });
    }
  }
  /** Called when current metadata/events invalidate the generation basis. */
  reconcile() {
    const old = this.active && this.attempts.get(this.active.id);
    if (!old || this.port.compatible(old.basis)) return;
    if (old.command.purpose === 'assist') {
      this.forget(old.basis.conversationId);
      return;
    }
    if (!this.busy) {
      if (this.state.outcome || this.state.error)
        this.set({ outcome: null, error: null, phase: 'idle' });
      return;
    }
    old.phase = old.phase === 'starting' ? 'unknown-start' : 'unknown-answer';
    this.cancel();
    this.show(old);
  }
  canAsk() {
    const basis = this.port.basis();
    if (
      !basis ||
      !this.active ||
      basis.conversationId !== this.active.id ||
      this.busy
    )
      return false;
    if (this.forgotten.has(basis.messageId)) return false;
    const old = this.attempts.get(basis.conversationId);
    return !old || old.basis.messageId !== basis.messageId;
  }
  canRecover() {
    const old = this.active && this.attempts.get(this.active.id),
      basis = this.port.basis();
    return (
      !!old &&
      !!basis &&
      sameBasis(old.basis, basis) &&
      this.auth.isCurrent(old.owner) &&
      ['unknown-start', 'unavailable', 'prepared'].includes(old.phase)
    );
  }
  async ask(service: PublicService, references?: PrivateReferences) {
    if (!this.canAsk() || Date.now() < this.deadline) return;
    const owner = this.auth.capture();
    const refs =
      references === undefined ? null : privateReferences.safeParse(references);
    if (
      refs &&
      (!refs.success ||
        owner.sessionKind !== 'customer' ||
        !this.state.privateAvailable)
    )
      return;
    const basis = this.port.basis()!;
    const attempt: Attempt = {
      owner,
      basis,
      command: {
        message_id: basis.messageId,
        request_key: crypto.randomUUID(),
        ...(refs?.success
          ? { purpose: 'assist' as const, ...refs.data }
          : { purpose: 'answer' as const }),
        service_type: service,
      },
      runId: null,
      phase: 'idle',
      outcome: null,
      error: null,
    };
    this.attempts.set(basis.conversationId, attempt);
    await this.perform(attempt, true);
  }
  /** Same immutable start can recover a prepared pointer; it never resumes work. */
  async recoverPrepared() {
    if (!this.canRecover() || Date.now() < this.deadline) return;
    const attempt = this.attempts.get(this.active!.id)!;
    if (attempt.runId) return;
    await this.perform(attempt, false);
  }
  async continuePrepared() {
    if (!this.canRecover() || Date.now() < this.deadline) return;
    const attempt = this.attempts.get(this.active!.id)!;
    if (!attempt.runId || attempt.phase !== 'prepared') return;
    await this.perform(attempt, true);
  }
  refresh() {
    if (!this.busy) this.port.refresh();
  }
  async checkOutcome() {
    const attempt = this.active && this.attempts.get(this.active.id);
    if (this.controller || Date.now() < this.deadline) return;
    if (
      !attempt?.runId ||
      this.busy ||
      attempt.phase !== 'unknown-answer' ||
      !this.port.compatible(attempt.basis) ||
      !this.auth.isCurrent(attempt.owner)
    )
      return;
    const serial = ++this.serial;
    const control = new AbortController();
    this.controller = control;
    const current = () =>
      this.serial === serial &&
      this.active?.id === attempt.basis.conversationId &&
      this.auth.isCurrent(attempt.owner) &&
      this.port.compatible(attempt.basis);
    try {
      const receipt = await agentApi(this.auth).receipt(attempt.runId, {
        boundary: attempt.owner,
        signal: control.signal,
        isRelevant: current,
      });
      if (!current() || receipt.state === 'unresolved') return;
      attempt.outcome = receipt;
      attempt.phase = receipt.state;
      attempt.error = null;
      this.show(attempt);
      if (receipt.state === 'published') this.port.refresh();
    } catch (error) {
      if (!current()) return;
      if (error instanceof ApiFailure && error.status === 429) {
        this.deadline = Math.max(
          this.deadline,
          Date.now() + (error.retryAfterMs ?? 5000),
        );
        this.show(attempt);
      }
      if (
        error instanceof ApiFailure &&
        ([401, 403, 404].includes(error.status ?? 0) ||
          error.code === 'SOURCE_UNAVAILABLE')
      ) {
        this.forget(attempt.basis.conversationId);
        this.port.revoke?.(error);
        if (error.code !== 'SOURCE_UNAVAILABLE') await this.auth.recheck();
      }
    } finally {
      if (this.serial === serial) this.controller = null;
    }
  }
  private async perform(attempt: Attempt, answer: boolean) {
    if (this.busy || !this.active || !this.auth.isCurrent(attempt.owner))
      return;
    const basis = this.port.basis();
    if (!basis || !sameBasis(attempt.basis, basis)) return;
    const token = this.active.token,
      serial = ++this.serial;
    const control = new AbortController();
    this.controller = control;
    this.release = this.port.hold();
    const current = () =>
      this.serial === serial &&
      this.active?.token === token &&
      this.active.id === attempt.basis.conversationId &&
      this.auth.isCurrent(attempt.owner) &&
      this.port.compatible(attempt.basis);
    const scope = {
      boundary: attempt.owner,
      signal: control.signal,
      isRelevant: current,
    };
    const api = agentApi(this.auth);
    let dispatched: 'none' | 'start' | 'answer' = 'none';
    attempt.error = null;
    attempt.phase = attempt.runId ? 'answering' : 'starting';
    this.show(attempt);
    try {
      const verify = async () => {
        const meta = await api.conversation(
          attempt.basis.conversationId,
          scope,
        );
        if (
          meta.conversation_id !== attempt.basis.conversationId ||
          meta.version !== attempt.basis.version ||
          meta.generation_epoch !== attempt.basis.epoch ||
          meta.language !== attempt.basis.language
        )
          throw new ApiFailure('stale', 'not-sent');
      };
      await verify();
      if (!attempt.runId) {
        dispatched = 'start';
        const started = await api.start(
          attempt.basis.conversationId,
          attempt.command,
          scope,
        );
        if (!current()) return;
        attempt.runId = started.run_id;
        attempt.phase = 'prepared';
        this.show(attempt);
      }
      if (!answer) return;
      await verify();
      dispatched = 'answer';
      attempt.phase = 'answering';
      this.show(attempt);
      const receipt = await api.resume(attempt.runId!, scope);
      if (!current()) return;
      attempt.outcome = receipt;
      attempt.phase = receipt.state;
      this.show(attempt);
    } catch (e) {
      if (this.serial !== serial || this.active?.token !== token) return;
      const error =
        e instanceof ApiFailure
          ? e
          : new ApiFailure(
              'invalid-response',
              dispatched === 'none' ? 'not-sent' : 'unknown',
            );
      attempt.error = error;
      if (
        error.status === 401 ||
        error.status === 403 ||
        error.status === 404
      ) {
        this.reset();
        await this.auth.recheck();
        return;
      }
      if (error.code === 'SOURCE_UNAVAILABLE') {
        this.forget(attempt.basis.conversationId);
        this.port.revoke?.(error);
        return;
      }
      if (error.code === 'AGENT_PRIVATE_UNAVAILABLE') {
        this.forget(attempt.basis.conversationId);
        this.set({ phase: 'unavailable', privateAvailable: false, error });
        return;
      }
      if (error.status === 429)
        this.deadline = Math.max(
          this.deadline,
          Date.now() + (error.retryAfterMs ?? 5000),
        );
      const unavailable =
        !attempt.runId &&
        (error.status === 429 ||
          [
            'AGENT_UNAVAILABLE',
            'AGENT_KNOWLEDGE_UNAVAILABLE',
            'AGENT_PRIVATE_UNAVAILABLE',
          ].includes(error.code));
      attempt.phase = unavailable
        ? 'unavailable'
        : error.outcome !== 'unknown'
          ? 'review'
          : dispatched === 'answer'
            ? 'unknown-answer'
            : dispatched === 'start'
              ? 'unknown-start'
              : 'review';
      this.show(attempt);
    } finally {
      if (this.serial === serial) {
        this.controller = null;
        const release = this.release;
        this.release = null;
        release?.();
        if (attempt.phase === 'published') this.port.refresh();
      }
    }
  }
}
