import { createContext, useContext } from 'react';
import { QueryClient } from '@tanstack/react-query';
import type { AuthCoordinator } from '../../app/auth/coordinator';
import type { BoundarySnapshot } from '../../api/contracts';
import { uuid } from '../../api/contracts';
import { ApiFailure } from '../../api/errors';
import { decodeCase, continuationFormSchema } from './detail-contracts';
import type { CaseDetail } from './detail-contracts';
interface Retained {
  message: string;
  editing: boolean;
  baseVersion: number;
  unknown: boolean;
}
interface State extends Retained {
  phase: 'idle' | 'loading' | 'ready' | 'sending' | 'unknown' | 'unavailable';
  detail: CaseDetail | null;
  error: ApiFailure | null;
  notice: 'changed' | 'recorded' | 'uncertain' | null;
  canRetry: boolean;
  retryAt: number;
}
export interface ContinueIntent {
  caseId: string;
  version: number;
  owner: BoundarySnapshot;
  message: string;
  sameIssue: boolean;
}
interface Command {
  readonly caseId: string;
  readonly owner: BoundarySnapshot;
  readonly body: Readonly<{
    expected_version: number;
    intent: 'same_issue';
    message: string;
    client_request_id: string;
  }>;
}
const empty = (): State => ({
  phase: 'idle',
  detail: null,
  error: null,
  notice: null,
  message: '',
  editing: false,
  baseVersion: 0,
  unknown: false,
  canRetry: false,
  retryAt: 0,
});
const failure = (e: unknown) =>
  e instanceof ApiFailure ? e : new ApiFailure('invalid-response', 'unknown');
const retainedKey = (id: string) => 'case-detail:/cases/' + id;
export class CaseDetailStore {
  private state = empty();
  private serial = 0;
  private id: string | null = null;
  private owner: BoundarySnapshot | null = null;
  private command: Command | null = null;
  private busy = false;
  private retryAt = 0;
  private flight: { id: string; epoch: number; promise: Promise<void> } | null =
    null;
  private listeners = new Set<() => void>();
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
    readonly auth: AuthCoordinator,
    private scope: string,
  ) {}
  getState = () => this.state;
  subscribe = (f: () => void) => {
    this.listeners.add(f);
    return () => {
      this.listeners.delete(f);
    };
  };
  private set(value: Partial<State>) {
    this.state = { ...this.state, ...value, retryAt: this.retryAt };
    this.listeners.forEach((f) => f());
  }
  connect() {
    const clear = () => {
      this.serial++;
      this.busy = false;
      this.command = null;
      this.owner = null;
      this.flight = null;
      this.queries.clear();
      this.set(empty());
    };
    const off = this.auth.onReset(clear),
      source = this.auth.onSourceUnavailable((error) => {
        clear();
        this.set({ phase: 'unavailable', error });
      });
    return () => {
      off();
      source();
    };
  }
  private current(id: string, serial: number, owner: BoundarySnapshot) {
    return (
      this.id === id && this.serial === serial && this.auth.isCurrent(owner)
    );
  }
  private remember() {
    if (this.id && this.owner && this.auth.isCurrent(this.owner))
      this.auth.keep(
        retainedKey(this.id),
        {
          message: this.state.message,
          editing: this.state.editing,
          baseVersion: this.state.baseVersion,
          unknown: this.state.unknown,
        } satisfies Retained,
        this.owner,
      );
  }
  private async read(id: string, serial: number, owner: BoundarySnapshot) {
    const detail = await this.queries.fetchQuery({
      queryKey: [
        this.scope,
        owner.boundaryId,
        owner.authEpoch,
        owner.sessionKind,
        owner.principalId,
        'case',
        id,
      ],
      queryFn: ({ signal }) =>
        this.auth.readResource(
          {
            path: '/cases/' + id,
            method: 'GET',
            boundary: owner,
            isRelevant: () => this.current(id, serial, owner),
            signal,
            successStatus: 200,
            decode: decodeCase,
          },
          'case-detail',
        ),
    });
    if (detail.id !== id) throw new ApiFailure('invalid-response', 'rejected');
    return detail;
  }
  private purge(id: string, owner: BoundarySnapshot, error: ApiFailure) {
    this.command = null;
    this.auth.forgetRetained(retainedKey(id), owner);
    this.set({ ...empty(), phase: 'unavailable', error });
  }
  open(id: string): Promise<void> {
    if (!uuid(id)) {
      this.detach();
      this.set({ ...empty(), phase: 'unavailable' });
      return Promise.resolve();
    }
    const owner = this.auth.capture();
    if (this.flight?.id === id && this.flight.epoch === owner.authEpoch)
      return this.flight.promise;
    if (this.busy && this.id === id) return Promise.resolve();
    if (this.id !== id) this.command = null;
    this.id = id;
    this.owner = owner;
    this.busy = false;
    const serial = ++this.serial;
    this.set({ ...empty(), phase: 'loading' });
    const promise = (async () => {
      try {
        const detail = await this.read(id, serial, owner);
        if (!this.current(id, serial, owner)) return;
        const held = this.auth.retained<Retained>(retainedKey(id), owner),
          command = this.command;
        const unknown = !!held?.unknown;
        this.set({
          ...empty(),
          detail,
          phase: unknown && command ? 'unknown' : 'ready',
          message: held?.message ?? '',
          baseVersion: detail.version,
          editing:
            !!held?.editing &&
            !unknown &&
            detail.status === 'closed' &&
            held.baseVersion === detail.version,
          unknown,
          canRetry:
            unknown &&
            !!command &&
            detail.status === 'closed' &&
            detail.version === command.body.expected_version &&
            this.auth.isCurrent(command.owner),
          notice: unknown
            ? 'uncertain'
            : held && held.baseVersion !== detail.version
              ? 'changed'
              : null,
        });
        this.remember();
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
  beginContinue() {
    const d = this.state.detail;
    if (
      this.busy ||
      this.command ||
      this.state.phase !== 'ready' ||
      !d ||
      d.status !== 'closed' ||
      this.auth.capture().sessionKind !== 'customer'
    )
      return;
    this.set({
      editing: true,
      error: null,
      notice: null,
      baseVersion: d.version,
      unknown: false,
    });
    this.remember();
  }
  input(message: string) {
    if (
      this.busy ||
      this.command ||
      !this.state.editing ||
      !this.owner ||
      !this.auth.isCurrent(this.owner)
    )
      return;
    this.set({ message });
    this.remember();
  }
  cancelEdit() {
    if (this.busy || this.command) return;
    this.set({ message: '', editing: false, notice: null });
    this.remember();
  }
  async continue(intent: ContinueIntent) {
    const d = this.state.detail;
    if (
      this.busy ||
      Date.now() < this.retryAt ||
      this.command ||
      this.state.phase !== 'ready' ||
      !this.state.editing ||
      !d ||
      d.id !== this.id ||
      d.id !== intent.caseId ||
      d.version !== intent.version ||
      d.status !== 'closed' ||
      !this.auth.isCurrent(intent.owner) ||
      intent.owner.sessionKind !== 'customer'
    )
      return;
    const form = continuationFormSchema.parse({
      message: intent.message,
      sameIssue: intent.sameIssue,
    });
    this.command = Object.freeze({
      caseId: d.id,
      owner: intent.owner,
      body: Object.freeze({
        expected_version: d.version,
        intent: 'same_issue' as const,
        message: form.message,
        client_request_id: crypto.randomUUID(),
      }),
    });
    this.set({ message: form.message, unknown: true, editing: false });
    this.remember();
    await this.dispatch(this.command);
  }
  async recover() {
    const id = this.id;
    if (!id || this.busy) return;
    const owner = this.auth.capture(),
      serial = ++this.serial,
      command = this.command;
    this.owner = owner;
    this.busy = true;
    this.set({ phase: 'loading', detail: null, canRetry: false, error: null });
    try {
      const detail = await this.read(id, serial, owner);
      if (!this.current(id, serial, owner)) return;
      this.set({
        detail,
        phase: command ? 'unknown' : 'ready',
        canRetry:
          !!command &&
          detail.status === 'closed' &&
          detail.version === command.body.expected_version &&
          this.auth.isCurrent(command.owner),
        notice: 'uncertain',
        editing: false,
        unknown: true,
      });
      this.remember();
    } catch (e) {
      if (this.current(id, serial, owner)) {
        const error = failure(e);
        if (error.status === 403 || error.status === 404)
          this.purge(id, owner, error);
        else
          this.set({
            phase: 'unavailable',
            detail: null,
            error,
            canRetry: false,
          });
      }
    } finally {
      if (this.serial === serial) this.busy = false;
    }
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
    await this.recover();
    if (
      this.command === command &&
      this.state.phase === 'unknown' &&
      this.state.canRetry &&
      this.auth.isCurrent(command.owner)
    )
      await this.dispatch(command);
  }
  async reviewAgain() {
    const id = this.id;
    if (!id || this.busy || this.state.detail?.status !== 'closed') return;
    const owner = this.auth.capture();
    this.command = null;
    this.set({ unknown: false, editing: false });
    this.remember();
    const pending = this.open(id),
      stamp = this.serial;
    await pending;
    if (this.current(id, stamp, owner) && this.state.phase === 'ready')
      this.set({ notice: 'changed' });
  }
  private async dispatch(command: Command) {
    const id = command.caseId;
    if (
      this.busy ||
      Date.now() < this.retryAt ||
      this.id !== id ||
      !this.auth.isCurrent(command.owner)
    )
      return;
    const serial = ++this.serial;
    this.busy = true;
    this.set({ phase: 'sending', canRetry: false, error: null });
    try {
      const detail = await this.auth.request({
        path: '/cases/' + id + '/continue',
        method: 'POST',
        boundary: command.owner,
        isRelevant: () => this.current(id, serial, command.owner),
        body: command.body,
        successStatus: 200,
        decode: decodeCase,
      });
      if (!this.current(id, serial, command.owner)) return;
      if (detail.id !== id || detail.version <= command.body.expected_version)
        throw new ApiFailure('invalid-response', 'unknown');
      this.command = null;
      this.set({
        ...empty(),
        phase: 'ready',
        detail,
        notice: 'recorded',
        baseVersion: detail.version,
      });
      this.remember();
    } catch (e) {
      if (!this.current(id, serial, command.owner)) return;
      const error = failure(e);
      this.busy = false;
      if (error.status === 429 && error.retryAfterMs !== null)
        this.retryAt = Math.max(this.retryAt, Date.now() + error.retryAfterMs);
      if (error.status === 403 || error.status === 404) {
        this.purge(id, command.owner, error);
        if (error.status === 403) await this.auth.recover();
        return;
      }
      if (error.outcome === 'unknown') {
        this.set({
          phase: 'unknown',
          unknown: true,
          notice: 'uncertain',
          error,
        });
        this.remember();
        await this.recover();
      } else {
        this.command = null;
        this.set({ phase: 'ready', unknown: false, error, editing: true });
        this.remember();
        if (error.status === 409) {
          this.set({ editing: false });
          this.remember();
          const pending = this.open(id),
            stamp = this.serial;
          await pending;
          if (
            this.current(id, stamp, command.owner) &&
            this.state.phase === 'ready'
          ) {
            this.set({ notice: 'changed', error, editing: false });
            this.remember();
          }
        }
      }
    } finally {
      if (this.serial === serial) this.busy = false;
    }
  }
  detach(id?: string) {
    if (id && this.id !== id) return;
    this.serial++;
    this.id = null;
    this.owner = null;
    this.command = null;
    this.busy = false;
    this.flight = null;
    this.queries.clear();
    this.set(empty());
  }
  discard() {
    if (this.id && this.owner)
      this.auth.forgetRetained(retainedKey(this.id), this.owner);
    this.detach();
  }
  dispose() {
    this.detach();
    this.listeners.clear();
  }
}
export const CaseDetailContext = createContext<CaseDetailStore | null>(null);
export const useCaseDetail = () => useContext(CaseDetailContext);
