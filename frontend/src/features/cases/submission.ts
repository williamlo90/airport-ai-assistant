import { createContext, useContext } from 'react';
import type { AuthCoordinator } from '../../app/auth/coordinator';
import type { BoundarySnapshot } from '../../api/contracts';
import { record, uuid } from '../../api/contracts';
import { ApiFailure } from '../../api/errors';
import type { CaseDraftStore } from './store';
import { canUseDraft, decodeDraft } from './contracts';

export interface ActionStatus {
  draft_id: string;
  state: 'pending' | 'committed' | 'cancelled' | 'expired';
  version: number;
  expires_at: string | null;
  case_id: string | null;
  result_ref: string | null;
}
const reference = (v: unknown): v is string =>
  typeof v === 'string' && /^CASE-[a-f0-9]{32}$/.test(v);
export function decodeAction(value: unknown): ActionStatus {
  const d = record(value);
  if (
    !uuid(d.draft_id) ||
    typeof d.state !== 'string' ||
    !['pending', 'committed', 'cancelled', 'expired'].includes(d.state) ||
    !Number.isSafeInteger(d.version) ||
    (d.version as number) < 1 ||
    (d.state === 'committed'
      ? !uuid(d.case_id) || !reference(d.result_ref) || d.expires_at !== null
      : (d.case_id !== null && !uuid(d.case_id)) ||
        d.result_ref !== null ||
        typeof d.expires_at !== 'string' ||
        !/^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/.test(d.expires_at) ||
        !Number.isFinite(Date.parse(d.expires_at)))
  )
    throw Error('Invalid action status');
  return {
    draft_id: d.draft_id,
    state: d.state as ActionStatus['state'],
    version: d.version as number,
    expires_at: d.expires_at as string | null,
    case_id: d.case_id as string | null,
    result_ref: d.result_ref as string | null,
  };
}
export function decodeCommit(value: unknown) {
  const d = record(value);
  if (
    d.state !== 'committed' ||
    !uuid(d.case_id) ||
    !uuid(d.message_id) ||
    !reference(d.result_ref)
  )
    throw Error('Invalid commit result');
  return {
    state: 'committed' as const,
    case_id: d.case_id,
    message_id: d.message_id,
    result_ref: d.result_ref,
  };
}
export interface ConfirmIntent {
  readonly draftId: string;
  readonly version: number;
  readonly hash: string;
  readonly owner: BoundarySnapshot;
}
interface Command extends ConfirmIntent {
  readonly key: string;
}
interface SubmissionState {
  phase:
    | 'idle'
    | 'loading'
    | 'review'
    | 'sending'
    | 'unknown'
    | 'receipt'
    | 'unavailable';
  receipt: ActionStatus | null;
  receiptContext: { draftId: string; conversationId: string } | null;
  error: ApiFailure | null;
  canRetry: boolean;
  reviewAgain: boolean;
  retryAt: number;
}
const empty = (): SubmissionState => ({
  phase: 'idle',
  receipt: null,
  receiptContext: null,
  error: null,
  canRetry: false,
  reviewAgain: false,
  retryAt: 0,
});
const failure = (e: unknown) =>
  e instanceof ApiFailure ? e : new ApiFailure('invalid-response', 'unknown');
const key = (id: string) => 'case-draft:/actions/' + id;

/** Commands are memory-only and never rebound to a replacement auth boundary. */
export class SubmissionStore {
  private state = empty();
  private listeners = new Set<() => void>();
  private serial = 0;
  private id: string | null = null;
  private command: Command | null = null;
  private busy = false;
  private retryAt = 0;
  private receiptOwner: BoundarySnapshot | null = null;
  private contextFlight: { serial: number; promise: Promise<void> } | null =
    null;
  private flight: { id: string; epoch: number; promise: Promise<void> } | null =
    null;
  constructor(
    readonly auth: AuthCoordinator,
    readonly drafts: CaseDraftStore,
  ) {}
  getState = () => this.state;
  subscribe = (f: () => void) => {
    this.listeners.add(f);
    return () => {
      this.listeners.delete(f);
    };
  };
  private set(value: Partial<SubmissionState>) {
    this.state = { ...this.state, ...value, retryAt: this.retryAt };
    this.listeners.forEach((f) => f());
  }
  connect() {
    const clear = () => {
      this.receiptOwner = null;
      this.contextFlight = null;
      this.serial++;
      this.command = null;
      this.busy = false;
      this.flight = null;
      this.set(empty());
    };
    const reset = this.auth.onReset(clear),
      source = this.auth.onSourceUnavailable((error) => {
        clear();
        this.set({ phase: 'unavailable', error });
      });
    return () => {
      reset();
      source();
    };
  }
  detach(id: string) {
    if (this.id !== id) return;
    this.receiptOwner = null;
    this.contextFlight = null;
    this.serial++;
    this.id = null;
    this.command = null;
    this.busy = false;
    this.flight = null;
    this.set(empty());
  }
  private current(id: string, serial: number, owner: BoundarySnapshot) {
    return (
      this.id === id && this.serial === serial && this.auth.isCurrent(owner)
    );
  }
  private async status(id: string, serial: number, owner: BoundarySnapshot) {
    const value = await this.auth.readResource(
      {
        path: '/actions/' + id,
        method: 'GET',
        boundary: owner,
        isRelevant: () => this.current(id, serial, owner),
        successStatus: 200,
        decode: decodeAction,
      },
      'case-draft',
    );
    if (value.draft_id !== id)
      throw new ApiFailure('invalid-response', 'rejected');
    return value;
  }
  private rememberUnknown(owner: BoundarySnapshot) {
    if (this.id && this.auth.isCurrent(owner))
      this.auth.keep(key(this.id), { unknown: true }, owner);
  }
  private receipt(value: ActionStatus, owner: BoundarySnapshot) {
    this.command = null;
    this.receiptOwner = owner;
    this.set({ ...empty(), phase: 'receipt', receipt: value });
  }
  receiptConversation(id: string): string | null {
    const { receipt, receiptContext } = this.state;
    return this.state.phase === 'receipt' &&
      this.id === id &&
      receipt?.draft_id === id &&
      receiptContext?.draftId === id &&
      this.receiptOwner &&
      this.auth.isCurrent(this.receiptOwner)
      ? receiptContext.conversationId
      : null;
  }
  /** Optional authorized context read; a receipt never depends on preview access. */
  loadReceiptContext(id: string): Promise<void> {
    const receipt = this.state.receipt,
      owner = this.receiptOwner,
      serial = this.serial;
    if (
      this.state.phase !== 'receipt' ||
      this.id !== id ||
      receipt?.draft_id !== id ||
      !owner ||
      !this.auth.isCurrent(owner)
    )
      return Promise.resolve();
    if (this.state.receiptContext) return Promise.resolve();
    if (this.contextFlight?.serial === serial)
      return this.contextFlight.promise;
    const current = () =>
      this.current(id, serial, owner) &&
      this.state.phase === 'receipt' &&
      this.state.receipt === receipt &&
      this.receiptOwner === owner;
    const promise = (async () => {
      try {
        const draft = await this.auth.readResource(
          {
            path: '/action-drafts/' + id,
            method: 'GET',
            boundary: owner,
            isRelevant: current,
            successStatus: 200,
            decode: decodeDraft,
          },
          'case-draft',
        );
        if (!current()) return;
        if (
          draft.draft_id !== id ||
          draft.state !== 'committed' ||
          draft.result_ref !== receipt.result_ref ||
          (draft.case_id !== null && draft.case_id !== receipt.case_id)
        )
          return;
        if (draft.action_type === 'request_handoff')
          this.set({
            receiptContext: {
              draftId: id,
              conversationId: draft.conversation_id,
            },
          });
      } catch {
        // This read authorizes only the optional continuation pointer. A denied
        // preview does not revoke an independently authorized action receipt.
      } finally {
        if (this.contextFlight?.serial === serial) this.contextFlight = null;
      }
    })();
    this.contextFlight = { serial, promise };
    return promise;
  }
  private purge(id: string, owner: BoundarySnapshot, error: ApiFailure) {
    this.command = null;
    this.auth.forgetRetained(key(id), owner);
    this.drafts.discard();
    this.set({ ...empty(), phase: 'unavailable', error });
  }
  open(id: string): Promise<void> {
    if (!uuid(id)) {
      this.serial++;
      this.id = null;
      this.command = null;
      this.set({ ...empty(), phase: 'unavailable' });
      return Promise.resolve();
    }
    const owner = this.auth.capture();
    if (this.flight?.id === id && this.flight.epoch === owner.authEpoch)
      return this.flight.promise;
    if (this.busy && this.id === id) return Promise.resolve();
    if (this.id !== id) this.command = null;
    this.id = id;
    this.busy = false;
    const serial = ++this.serial;
    this.set({ ...empty(), phase: 'loading' });
    const promise = (async () => {
      try {
        const status = await this.status(id, serial, owner);
        if (!this.current(id, serial, owner)) return;
        if (status.state === 'committed') {
          this.receipt(status, owner);
          return;
        }
        await this.drafts.open(id);
        if (!this.current(id, serial, owner)) return;
        const draft = this.drafts.getState().draft;
        if (!draft || draft.draft_id !== id)
          throw (
            this.drafts.getState().error ??
            new ApiFailure('invalid-response', 'rejected')
          );
        if (draft.state === 'committed') {
          const receipt = await this.status(id, serial, owner);
          if (!this.current(id, serial, owner)) return;
          if (receipt.state !== 'committed')
            throw new ApiFailure('invalid-response', 'unknown');
          this.receipt(receipt, owner);
          return;
        }
        const held = this.auth.retained<{ unknown: boolean }>(key(id), owner);
        const command = this.command;
        if (
          held?.unknown &&
          command &&
          this.auth.isCurrent(command.owner) &&
          draft.state === 'pending' &&
          draft.version === command.version &&
          draft.payload_hash === command.hash
        ) {
          this.set({ ...empty(), phase: 'unknown', canRetry: true });
        } else {
          this.command = null;
          this.set({
            ...empty(),
            phase: 'review',
            reviewAgain: !!held?.unknown,
          });
        }
      } catch (e) {
        if (this.current(id, serial, owner)) {
          const error = failure(e);
          if (error.status === 403 || error.status === 404)
            this.purge(id, owner, error);
          else this.set({ ...empty(), phase: 'unavailable', error });
        }
      } finally {
        if (this.serial === serial) this.flight = null;
      }
    })();
    this.flight = { id, epoch: owner.authEpoch, promise };
    return promise;
  }
  async confirm(intent: ConfirmIntent) {
    const draft = this.drafts.getState(),
      d = draft.draft;
    if (
      this.busy ||
      Date.now() < this.retryAt ||
      this.state.phase !== 'review' ||
      draft.phase !== 'review' ||
      draft.localFilePending ||
      !d ||
      d.draft_id !== this.id ||
      d.draft_id !== intent.draftId ||
      d.version !== intent.version ||
      d.payload_hash !== intent.hash ||
      !this.auth.isCurrent(intent.owner) ||
      !canUseDraft(
        intent.owner.sessionKind,
        d.action_type,
        d.payload,
        d.case_id,
      ) ||
      d.state !== 'pending' ||
      this.drafts.expired() ||
      draft.editing ||
      draft.uncertain
    )
      return;
    this.command = Object.freeze({ ...intent, key: crypto.randomUUID() });
    await this.dispatch(this.command);
  }
  async retry() {
    const command = this.command;
    if (
      this.busy ||
      Date.now() < this.retryAt ||
      this.state.phase !== 'unknown' ||
      !this.state.canRetry ||
      !command ||
      !this.auth.isCurrent(command.owner)
    )
      return;
    // A second status check belongs to this explicit retry intent. No POST on a GET-only recovery.
    await this.recover();
    if (
      this.command === command &&
      this.state.phase === 'unknown' &&
      this.state.canRetry &&
      this.auth.isCurrent(command.owner)
    )
      await this.dispatch(command);
  }
  async recover() {
    const id = this.id;
    if (!id || this.busy) return;
    const owner = this.auth.capture(),
      serial = ++this.serial,
      command = this.command;
    this.busy = true;
    this.set({ phase: 'unknown', canRetry: false, error: null });
    try {
      const status = await this.status(id, serial, owner);
      if (!this.current(id, serial, owner)) return;
      if (status.state === 'committed') {
        this.receipt(status, owner);
        return;
      }
      if (
        status.state === 'pending' &&
        command &&
        command.draftId === id &&
        this.auth.isCurrent(command.owner) &&
        status.version === command.version
      ) {
        this.set({ phase: 'unknown', canRetry: true });
      } else {
        this.command = null;
        this.busy = false;
        await this.freshReview(id, owner);
      }
    } catch (e) {
      if (this.current(id, serial, owner)) {
        const error = failure(e);
        if (error.status === 403 || error.status === 404)
          this.purge(id, owner, error);
        else this.set({ phase: 'unknown', error, canRetry: false });
      }
    } finally {
      if (this.serial === serial) this.busy = false;
    }
  }
  private async freshReview(
    id: string,
    owner: BoundarySnapshot,
    error?: ApiFailure,
  ) {
    const pending = this.open(id),
      stamp = this.serial;
    await pending;
    if (this.current(id, stamp, owner) && this.state.phase === 'review')
      this.set({ reviewAgain: true, ...(error ? { error } : {}) });
  }
  private async dispatch(command: Command) {
    if (
      this.busy ||
      Date.now() < this.retryAt ||
      this.id !== command.draftId ||
      !this.auth.isCurrent(command.owner)
    )
      return;
    const serial = ++this.serial,
      id = command.draftId;
    this.busy = true;
    this.set({ ...empty(), phase: 'sending' });
    this.rememberUnknown(command.owner);
    try {
      const result = await this.auth.request({
        path: '/action-drafts/' + id + '/confirm',
        method: 'POST',
        boundary: command.owner,
        isRelevant: () => this.current(id, serial, command.owner),
        idempotencyKey: command.key,
        body: { expected_version: command.version, payload_hash: command.hash },
        successStatus: 201,
        decode: decodeCommit,
      });
      if (!this.current(id, serial, command.owner)) return;
      this.receipt(
        {
          draft_id: id,
          state: 'committed',
          version: command.version,
          expires_at: null,
          case_id: result.case_id,
          result_ref: result.result_ref,
        },
        command.owner,
      );
    } catch (e) {
      if (!this.current(id, serial, command.owner)) return;
      const error = failure(e);
      if (error.status === 429 && error.retryAfterMs !== null)
        this.retryAt = Math.max(this.retryAt, Date.now() + error.retryAfterMs);
      if (error.status === 403 || error.status === 404) {
        this.purge(id, command.owner, error);
        if (error.status === 403) await this.auth.recover();
        return;
      }
      this.busy = false;
      if (error.outcome === 'unknown') {
        this.set({ phase: 'unknown', error, canRetry: false });
        await this.recover();
      } else {
        this.command = null;
        this.auth.forgetRetained(key(id), command.owner);
        if (error.status === 409 || error.status === 410) {
          await this.freshReview(id, command.owner, error);
        } else
          this.set({
            phase: 'review',
            error,
            reviewAgain: true,
            canRetry: false,
          });
      }
    } finally {
      if (this.serial === serial) this.busy = false;
    }
  }
  dispose() {
    this.receiptOwner = null;
    this.contextFlight = null;
    this.serial++;
    this.command = null;
    this.listeners.clear();
  }
}
export const SubmissionContext = createContext<SubmissionStore | null>(null);
export const useSubmission = () => useContext(SubmissionContext);
