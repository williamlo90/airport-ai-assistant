import { createApiClient } from '../../api/client';
import type { BusinessRequest } from '../../api/client';
import {
  decodeSession,
  decodeEmpty,
  decodeChallenge,
} from '../../api/contracts';
import type { BoundarySnapshot, SessionView } from '../../api/contracts';
import { ApiFailure } from '../../api/errors';
import { decodeStaff, parseSignal } from './contracts';
import type { Membership, SignalPort } from './contracts';
export type AuthState =
  | Readonly<{
      phase: 'checking' | 'guest' | 'unavailable' | 'transition';
      epoch: number;
    }>
  | Readonly<{
      phase: 'ready';
      epoch: number;
      boundaryId: string;
      kind: BoundarySnapshot['sessionKind'];
      roles: readonly string[];
    }>;
type Change =
  | { kind: 'bootstrap' }
  | { kind: 'logout' }
  | { kind: 'verify'; challengeId: string; code: string }
  | { kind: 'staff-login'; contact: string; password: string };
interface Options {
  readProof: () => string | null;
  fetch?: typeof fetch;
  signals?: SignalPort;
  randomId?: () => string;
  enabled?: boolean;
}
interface Entry {
  owner: BoundarySnapshot;
  value: unknown;
  visible: boolean;
  kind: 'draft' | 'command';
}
export class AuthCoordinator {
  private state: AuthState = Object.freeze({ phase: 'unavailable', epoch: 0 });
  private listeners = new Set<() => void>();
  private resetListeners = new Set<() => void>();
  private sourceListeners = new Set<(error: ApiFailure) => void>();
  private entries = new Map<string, Entry>();
  private active: BoundarySnapshot | null = null;
  private previous: BoundarySnapshot | null = null;
  private memberships: readonly Membership[] = [];
  private generation = 0;
  private disposed = false;
  private started = false;
  private checkFlight: Promise<AuthState> | null = null;
  private checkAbort: AbortController | null = null;
  private businessControllers = new Set<AbortController>();
  private sessionControllers = new Set<AbortController>();
  private remote = new Set<string>();
  private ended = new Set<string>();
  private localId: string | null = null;
  private unsubscribe: (() => void) | null = null;
  private readonly client: ReturnType<typeof createApiClient>;
  private readonly randomId: () => string;
  constructor(private readonly options: Options) {
    this.randomId = options.randomId ?? (() => crypto.randomUUID());
    this.client = createApiClient({
      ...(options.fetch ? { fetch: options.fetch } : {}),
      boundary: {
        isCurrent: () => false,
        readCsrfProof: options.readProof,
        invalidate: () => {},
      },
    });
  }
  getState = () => this.state;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  onReset = (listener: () => void) => {
    this.resetListeners.add(listener);
    return () => {
      this.resetListeners.delete(listener);
    };
  };
  onSourceUnavailable = (listener: (error: ApiFailure) => void) => {
    this.sourceListeners.add(listener);
    return () => {
      this.sourceListeners.delete(listener);
    };
  };
  private emit(state: AuthState) {
    if (this.disposed) return;
    this.state = Object.freeze(state);
    this.listeners.forEach((fn) => fn());
  }
  private sameIdentity(a: BoundarySnapshot, b: BoundarySnapshot) {
    return (
      a.boundaryId === b.boundaryId &&
      a.csrfProof === b.csrfProof &&
      a.sessionKind === b.sessionKind &&
      a.principalId === b.principalId
    );
  }
  isCurrent = (owner: BoundarySnapshot) =>
    this.state.phase === 'ready' &&
    !!this.active &&
    this.sameIdentity(owner, this.active) &&
    owner.authEpoch === this.active.authEpoch;
  capture(): BoundarySnapshot {
    if (!this.active || !this.isCurrent(this.active))
      throw new ApiFailure('stale', 'not-sent');
    if (this.options.readProof() !== this.active.csrfProof) {
      this.invalidate();
      throw new ApiFailure('stale', 'not-sent');
    }
    return this.active;
  }
  private hold(phase: 'checking' | 'unavailable' | 'transition', hard = false) {
    this.generation++;
    this.active = null;
    this.memberships = [];
    this.checkAbort?.abort();
    this.checkAbort = null;
    this.checkFlight = null;
    for (const control of this.businessControllers) control.abort();
    this.businessControllers.clear();
    for (const control of this.sessionControllers) control.abort();
    this.sessionControllers.clear();
    for (const [key, entry] of this.entries) {
      if (hard || entry.kind === 'command') this.entries.delete(key);
      else entry.visible = false;
    }
    if (hard) this.previous = null;
    this.emit({ phase, epoch: this.generation });
    this.resetListeners.forEach((fn) => fn());
  }
  private clearPrivate() {
    this.entries.clear();
    this.previous = null;
    this.resetListeners.forEach((fn) => fn());
  }
  start(): Promise<AuthState> {
    if (this.started) return this.checkFlight ?? Promise.resolve(this.state);
    this.started = true;
    if (this.options.enabled === false || this.disposed)
      return Promise.resolve(this.state);
    this.unsubscribe = this.options.signals?.subscribe(this.receive) ?? null;
    return this.recheck();
  }
  recheck = (): Promise<AuthState> => {
    if (
      this.disposed ||
      this.options.enabled === false ||
      this.localId ||
      this.remote.size
    )
      return Promise.resolve(this.state);
    if (this.checkFlight) return this.checkFlight;
    this.hold('checking');
    const stamp = this.generation,
      control = new AbortController();
    this.checkAbort = control;
    const run = async () => {
      for (let attempt = 0; attempt < 2; attempt++) {
        const proof = this.options.readProof();
        const permit = {
          csrfProof: proof,
          isCurrent: () =>
            !this.disposed &&
            stamp === this.generation &&
            !this.localId &&
            !this.remote.size,
        };
        let hadSession = false;
        try {
          const session = await this.client.session({
            operation: 'inspect',
            permit,
            signal: control.signal,
            successStatus: 200,
            decode: decodeSession,
          });
          hadSession = true;
          if (!permit.isCurrent()) return this.state;
          if (!proof) throw new ApiFailure('stale', 'not-sent');
          let memberships: readonly Membership[] = [];
          if (session.session_kind === 'staff') {
            const staff = await this.client.session({
              operation: 'inspect-staff',
              permit,
              signal: control.signal,
              successStatus: 200,
              decode: decodeStaff,
            });
            if (staff.principal_id !== session.principal_id)
              throw new ApiFailure('stale', 'not-sent');
            memberships = staff.memberships;
          }
          if (!permit.isCurrent()) return this.state;
          if (this.options.readProof() !== proof) continue;
          const old = this.previous;
          const same =
            old &&
            old.csrfProof === proof &&
            old.sessionKind === session.session_kind &&
            old.principalId === session.principal_id;
          if (!same) this.clearPrivate();
          const snapshot = Object.freeze({
            boundaryId: same ? old.boundaryId : this.randomId(),
            authEpoch: stamp,
            sessionKind: session.session_kind,
            principalId: session.principal_id,
            csrfProof: proof,
          });
          this.active = this.previous = snapshot;
          this.memberships = memberships;
          const roles = Object.freeze([
            ...new Set(memberships.flatMap((m) => [...m.roles])),
          ]);
          this.emit({
            phase: 'ready',
            epoch: stamp,
            boundaryId: snapshot.boundaryId,
            kind: session.session_kind,
            roles,
          });
          return this.state;
        } catch (error) {
          if (!permit.isCurrent()) return this.state;
          if (this.options.readProof() !== proof) continue;
          this.clearPrivate();
          this.emit({
            phase:
              error instanceof ApiFailure && error.status === 401 && !hadSession
                ? 'guest'
                : 'unavailable',
            epoch: stamp,
          });
          return this.state;
        }
      }
      if (stamp === this.generation) {
        this.clearPrivate();
        this.emit({ phase: 'unavailable', epoch: stamp });
      }
      return this.state;
    };
    const flight = run().finally(() => {
      if (this.checkFlight === flight) this.checkFlight = null;
      if (this.checkAbort === control) this.checkAbort = null;
    });
    this.checkFlight = flight;
    return flight;
  };
  private invalidate() {
    if (this.disposed || this.state.phase !== 'ready') return;
    this.hold('unavailable', true);
    queueMicrotask(() => {
      void this.recheck();
    });
  }
  private receive = (raw: unknown) => {
    const signal = parseSignal(raw);
    if (
      !signal ||
      this.disposed ||
      signal.transitionId === this.localId ||
      this.ended.has(signal.transitionId)
    )
      return;
    if (signal.type === 'auth_transition_begin') {
      if (this.remote.has(signal.transitionId)) return;
      this.remote.add(signal.transitionId);
      this.hold('transition', true);
    } else {
      this.remote.delete(signal.transitionId);
      this.ended.add(signal.transitionId);
      if (this.ended.size > 64)
        this.ended.delete(this.ended.values().next().value!);
      if (!this.remote.size && !this.localId) void this.recheck();
    }
  };
  recover(): Promise<AuthState> {
    if (this.localId) return Promise.resolve(this.state);
    this.remote.clear();
    return this.recheck();
  }
  private publish(
    type: 'auth_transition_begin' | 'auth_transition_end',
    transitionId: string,
  ) {
    try {
      this.options.signals?.publish({ type, transitionId });
    } catch {
      /* Proof binding also works without cross-tab delivery. */
    }
  }
  async change(change: Change): Promise<{
    state: AuthState;
    outcome: 'confirmed' | 'rejected' | 'unknown';
    resumeFromBoundaryId: string | null;
    retryFromBoundaryId: string | null;
    failure: ApiFailure | null;
  }> {
    if (
      this.disposed ||
      this.localId ||
      this.remote.size ||
      this.options.enabled === false
    )
      throw new ApiFailure('stale', 'not-sent');
    const before = this.active;
    if (
      change.kind === 'bootstrap'
        ? this.state.phase !== 'guest'
        : !before || !this.isCurrent(before)
    )
      throw new ApiFailure('stale', 'not-sent');
    if (
      (change.kind === 'verify' || change.kind === 'staff-login') &&
      before?.sessionKind !== 'anonymous'
    )
      throw new ApiFailure('invalid-request', 'not-sent');
    if (change.kind === 'verify' && !/^\d{8}$/.test(change.code))
      throw new ApiFailure('invalid-request', 'not-sent');
    const proof = before?.csrfProof ?? this.options.readProof();
    if (this.options.readProof() !== proof) {
      this.invalidate();
      throw new ApiFailure('stale', 'not-sent');
    }
    const id = this.randomId();
    this.localId = id;
    this.hold('transition', true);
    const stamp = this.generation;
    this.publish('auth_transition_begin', id);
    const control = new AbortController();
    this.sessionControllers.add(control);
    const permit = {
      csrfProof: proof,
      isCurrent: () =>
        !this.disposed &&
        this.localId === id &&
        stamp === this.generation &&
        !this.remote.size,
    };
    let outcome: 'confirmed' | 'rejected' | 'unknown';
    let verified: SessionView | null = null;
    let failure: ApiFailure | null = null;
    let verifiedProof: string | null = null;
    try {
      if (change.kind === 'logout')
        await this.client.session({
          operation: 'logout',
          permit,
          body: {},
          signal: control.signal,
          successStatus: 204,
          decode: decodeEmpty,
        });
      else if (change.kind === 'bootstrap')
        await this.client.session({
          operation: 'bootstrap',
          permit,
          body: {},
          signal: control.signal,
          successStatus: 200,
          decode: decodeSession,
        });
      else if (change.kind === 'staff-login')
        verified = await this.client.session({
          operation: 'staff-login',
          permit,
          body: { contact: change.contact, password: change.password },
          signal: control.signal,
          successStatus: 200,
          decode: (value) => {
            const view = decodeSession(value);
            if (view.session_kind !== 'staff')
              throw new Error('Invalid staff login result');
            return view;
          },
        });
      else
        verified = await this.client.session({
          operation: 'verify',
          permit,
          challengeId: change.challengeId,
          body: { code: change.code },
          signal: control.signal,
          successStatus: 200,
          decode: (value) => {
            const view = decodeSession(value);
            if (view.session_kind !== 'customer')
              throw new Error('Invalid verification result');
            return view;
          },
        });
      verifiedProof = this.options.readProof();
      outcome = 'confirmed';
    } catch (error) {
      failure =
        error instanceof ApiFailure
          ? error
          : new ApiFailure('network', 'unknown');
      outcome =
        error instanceof ApiFailure &&
        error.outcome !== 'unknown' &&
        error.kind !== 'stale'
          ? 'rejected'
          : 'unknown';
    } finally {
      this.sessionControllers.delete(control);
      if (this.localId === id) this.localId = null;
      this.ended.add(id);
      this.publish('auth_transition_end', id);
    }
    const state = await this.recheck();
    const resume =
      outcome === 'confirmed' &&
      change.kind === 'verify' &&
      before?.sessionKind === 'anonymous' &&
      verified?.session_kind === 'customer' &&
      this.active?.sessionKind === 'customer' &&
      this.active.principalId === verified.principal_id &&
      verifiedProof !== null &&
      this.active.csrfProof === verifiedProof &&
      stamp === this.generation - 1
        ? before.boundaryId
        : null;
    const retry =
      outcome === 'rejected' &&
      change.kind === 'verify' &&
      before?.sessionKind === 'anonymous' &&
      this.active?.sessionKind === 'anonymous' &&
      this.active.csrfProof === before.csrfProof &&
      stamp === this.generation - 1
        ? before.boundaryId
        : null;
    return {
      state,
      outcome,
      resumeFromBoundaryId: resume,
      retryFromBoundaryId: retry,
      failure,
    };
  }
  membershipsFor(owner: BoundarySnapshot): readonly Membership[] {
    this.capture();
    if (!this.isCurrent(owner)) throw new ApiFailure('stale', 'not-sent');
    return this.memberships;
  }
  async requestChallenge(contact: string) {
    const owner = this.capture();
    if (owner.sessionKind !== 'anonymous')
      throw new ApiFailure('invalid-request', 'not-sent');
    const control = new AbortController();
    this.sessionControllers.add(control);
    try {
      return await this.client.session({
        operation: 'challenge',
        permit: {
          csrfProof: owner.csrfProof,
          isCurrent: () => this.isCurrent(owner),
        },
        body: { contact, purpose: 'customer_login' },
        signal: control.signal,
        successStatus: 202,
        decode: decodeChallenge,
      });
    } catch (error) {
      if (
        this.isCurrent(owner) &&
        (this.options.readProof() !== owner.csrfProof ||
          (error instanceof ApiFailure && error.status === 401))
      )
        this.invalidate();
      else if (
        this.isCurrent(owner) &&
        error instanceof ApiFailure &&
        error.status === 403
      )
        await this.recheck();
      throw error;
    } finally {
      this.sessionControllers.delete(control);
    }
  }
  async request<T>(request: BusinessRequest<T>): Promise<T> {
    if (!this.isCurrent(request.boundary))
      throw new ApiFailure('stale', 'not-sent');
    const control = new AbortController(),
      abort = () => control.abort();
    if (request.signal?.aborted) control.abort();
    else request.signal?.addEventListener('abort', abort, { once: true });
    this.businessControllers.add(control);
    const client = createApiClient({
      ...(this.options.fetch ? { fetch: this.options.fetch } : {}),
      boundary: {
        isCurrent: this.isCurrent,
        readCsrfProof: this.options.readProof,
        invalidate: () => {
          if (this.isCurrent(request.boundary)) this.invalidate();
        },
      },
    });
    try {
      return await client.request({ ...request, signal: control.signal });
    } catch (error) {
      if (
        error instanceof ApiFailure &&
        error.code === 'SOURCE_UNAVAILABLE' &&
        this.isCurrent(request.boundary) &&
        (!request.isRelevant || request.isRelevant())
      ) {
        this.entries.clear();
        this.sourceListeners.forEach((listener) => listener(error));
      }
      throw error;
    } finally {
      this.businessControllers.delete(control);
      request.signal?.removeEventListener('abort', abort);
    }
  }
  keep(
    key: string,
    value: unknown,
    owner: BoundarySnapshot,
    kind: 'draft' | 'command' = 'draft',
  ) {
    this.capture();
    if (!this.isCurrent(owner)) throw new ApiFailure('stale', 'not-sent');
    this.entries.set(key, { owner, value, visible: true, kind });
  }
  forgetRetained(key: string, owner: BoundarySnapshot) {
    if (this.isCurrent(owner)) this.entries.delete(key);
  }
  retained<T>(key: string, owner: BoundarySnapshot): T | undefined {
    if (!this.isCurrent(owner) || this.options.readProof() !== owner.csrfProof)
      return undefined;
    const entry = this.entries.get(key);
    return entry?.visible &&
      entry.owner.authEpoch === owner.authEpoch &&
      this.sameIdentity(entry.owner, owner)
      ? (entry.value as T)
      : undefined;
  }
  async readResource<T>(
    request: BusinessRequest<T>,
    namespace?: 'case-draft' | 'case-detail',
  ): Promise<T> {
    if (request.method !== 'GET')
      throw new ApiFailure('invalid-request', 'not-sent');
    const retentionKey = namespace
      ? namespace + ':' + request.path
      : request.path;
    const entry = this.entries.get(retentionKey);
    if (entry) entry.visible = false;
    try {
      const response = await this.request(request);
      const old = this.entries.get(retentionKey);
      if (
        old?.kind === 'draft' &&
        this.sameIdentity(old.owner, request.boundary) &&
        this.isCurrent(request.boundary)
      )
        this.entries.set(retentionKey, {
          ...old,
          owner: request.boundary,
          visible: true,
        });
      return response;
    } catch (error) {
      if (
        error instanceof ApiFailure &&
        (error.status === 403 || error.status === 404)
      )
        this.entries.delete(retentionKey);
      throw error;
    }
  }
  async mutateResource<T>(
    resourcePath: string,
    request: BusinessRequest<T>,
  ): Promise<T> {
    try {
      return await this.request(request);
    } catch (error) {
      if (error instanceof ApiFailure && error.status === 404)
        this.entries.delete(resourcePath);
      if (error instanceof ApiFailure && error.status === 403) {
        const entry = this.entries.get(resourcePath);
        if (entry) entry.visible = false;
        await this.recheck();
      }
      throw error;
    }
  }
  dispose() {
    if (this.disposed) return;
    if (this.localId) this.publish('auth_transition_end', this.localId);
    this.hold('unavailable', true);
    this.disposed = true;
    this.unsubscribe?.();
    this.unsubscribe = null;
    this.listeners.clear();
    this.resetListeners.clear();
    this.sourceListeners.clear();
  }
}
