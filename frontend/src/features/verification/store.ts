import { z } from 'zod';
import type { AuthCoordinator } from '../../app/auth/coordinator';
import type { BoundarySnapshot } from '../../api/contracts';
import { ApiFailure } from '../../api/errors';
import type { ChatStore } from '../chat/store';
import type { MessageInput } from '../chat/contracts';
export const contactSchema = z.object({ contact: z.email().max(320) });
export const codeSchema = z.object({ code: z.string().regex(/^[0-9]{8}$/) });
export interface VerificationState {
  phase:
    | 'checking'
    | 'email'
    | 'code'
    | 'requesting'
    | 'verifying'
    | 'review'
    | 'continuing'
    | 'continue-unknown'
    | 'unknown'
    | 'unavailable'
    | 'staff'
    | 'guest';
  contact: string;
  challengeId: string | null;
  report: MessageInput | null;
  contactVerified: boolean;
  continuedId: string | null;
  error: ApiFailure | null;
  blockedUntil: number | null;
}
const empty = (): VerificationState => ({
  phase: 'checking',
  contact: '',
  challengeId: null,
  report: null,
  contactVerified: false,
  continuedId: null,
  error: null,
  blockedUntil: null,
});
const failure = (e: unknown) =>
  e instanceof ApiFailure ? e : new ApiFailure('network', 'unknown');
const same = (a: BoundarySnapshot, b: BoundarySnapshot) =>
  a.boundaryId === b.boundaryId &&
  a.csrfProof === b.csrfProof &&
  a.sessionKind === b.sessionKind &&
  a.principalId === b.principalId;
export class VerificationStore {
  private state = empty();
  private owner: BoundarySnapshot | null = null;
  private held: {
    owner: BoundarySnapshot;
    state: VerificationState;
    grant: boolean;
  } | null = null;
  private originId: string | null = null;
  private serial = 0;
  private verifyAttempt = 0;
  private working = false;
  private verifying = false;
  private resumeGranted = false;
  private listeners = new Set<() => void>();
  private waitUntil: number | null = null;
  private waitFailure: ApiFailure | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  constructor(
    readonly auth: AuthCoordinator,
    readonly chat: ChatStore,
    private now = () => Date.now(),
  ) {}
  getState = () => this.state;
  subscribe = (fn: () => void) => {
    this.listeners.add(fn);
    return () => {
      this.listeners.delete(fn);
    };
  };
  private set(value: Partial<VerificationState>) {
    if (this.waitUntil !== null && this.now() < this.waitUntil) {
      value = { ...value, blockedUntil: this.waitUntil };
      if (
        value.error === null &&
        ['guest', 'email', 'code'].includes(value.phase ?? this.state.phase)
      )
        value.error = this.waitFailure;
    }
    this.state = { ...this.state, ...value };
    this.listeners.forEach((fn) => fn());
  }
  private cooldown(error: ApiFailure) {
    if (error.status !== 429 || error.retryAfterMs === null) return;
    const until = this.now() + error.retryAfterMs;
    this.waitUntil = until;
    this.waitFailure = error;
    this.set({ blockedUntil: until });
    if (this.timer) clearTimeout(this.timer);
    const release = () => {
      const remaining = (this.waitUntil ?? this.now()) - this.now();
      if (remaining > 0) {
        this.timer = setTimeout(release, Math.min(remaining, 2147483647));
        return;
      }
      this.timer = null;
      this.waitUntil = null;
      this.waitFailure = null;
      this.set({ blockedUntil: null });
    };
    this.timer = setTimeout(release, Math.min(error.retryAfterMs, 2147483647));
  }

  blocked() {
    return (
      this.state.blockedUntil !== null && this.now() < this.state.blockedUntil
    );
  }
  private refresh = () => {
    const a = this.auth.getState();
    if (this.verifying) return;
    if (a.phase === 'ready') {
      let next: BoundarySnapshot;
      try {
        next = this.auth.capture();
      } catch {
        this.owner = null;
        this.held = null;
        this.resumeGranted = false;
        this.set({ ...empty(), phase: 'unavailable' });
        return;
      }
      if (this.owner && same(this.owner, next)) {
        this.owner = next;
        return;
      }
      const held = this.held;
      this.held = null;
      this.owner = next;
      this.resumeGranted = false;
      if (held && same(held.owner, next)) {
        this.resumeGranted = held.grant;
        this.set({
          ...held.state,
          phase:
            held.state.phase === 'requesting'
              ? 'email'
              : held.state.phase === 'continuing'
                ? this.chat.getState().phase === 'unknown-create'
                  ? 'continue-unknown'
                  : 'review'
                : held.state.phase,
          contactVerified: held.state.contactVerified,
        });
        return;
      }
      this.set({ ...empty(), phase: a.kind === 'staff' ? 'staff' : 'email' });
    } else if (a.phase === 'guest' || a.phase === 'unavailable') {
      this.owner = null;
      this.held = null;
      this.originId = null;
      this.resumeGranted = false;
      this.set({ ...empty(), phase: a.phase });
    }
  };
  connect() {
    const reset = this.auth.onReset(() => {
      if (
        !this.verifying &&
        this.owner &&
        this.auth.getState().phase === 'checking'
      )
        this.held = {
          owner: this.owner,
          state: this.state,
          grant: this.resumeGranted,
        };
      else this.held = null;
      this.serial++;
      this.owner = null;
      this.resumeGranted = false;
      this.working = false;
      this.set({ ...empty(), phase: 'checking' });
    });
    const off = this.auth.subscribe(this.refresh);
    this.refresh();
    return () => {
      reset();
      off();
      this.serial++;
      this.verifyAttempt++;
      this.verifying = false;
      this.working = false;
      if (this.timer) clearTimeout(this.timer);
      this.timer = null;
      this.held = null;
      this.owner = null;
      this.resumeGranted = false;
      this.state = empty();
    };
  }
  private capture() {
    const next = this.auth.capture();
    if (!this.owner || !same(this.owner, next))
      throw new ApiFailure('stale', 'not-sent');
    this.owner = next;
    return next;
  }
  private report() {
    if (this.state.report)
      return {
        body: this.state.report.body,
        service: this.state.report.service,
      };
    try {
      return this.chat.plainReport();
    } catch {
      return { body: '', service: '' as const };
    }
  }
  prepareFromChat() {
    const meta = this.chat.getState().meta;
    if (!meta || this.chat.getState().phase !== 'ready') return;
    const owner = this.auth.capture();
    if (owner.sessionKind === 'staff') return;
    const report = this.chat.plainReport();
    if (
      this.originId === meta.conversation_id &&
      this.owner &&
      same(this.owner, owner)
    ) {
      if (this.state.phase === 'email' || this.state.phase === 'code')
        this.set({ report });
      return;
    }
    const retainWait = this.owner && same(this.owner, owner);
    const blockedUntil = retainWait ? this.state.blockedUntil : null,
      error =
        retainWait && this.state.error?.status === 429
          ? this.state.error
          : null;
    this.serial++;
    this.verifyAttempt++;
    this.originId = meta.conversation_id;
    this.owner = owner;
    this.held = null;
    this.resumeGranted = false;
    this.set({ ...empty(), phase: 'email', report, blockedUntil, error });
  }
  contactDraft(contact: string) {
    if (this.state.phase === 'email') this.set({ contact });
  }
  async start() {
    if (this.working || this.blocked()) return;
    this.working = true;
    try {
      const result = await this.auth.change({ kind: 'bootstrap' });
      if (result.failure) {
        this.set({ error: result.failure });
        this.cooldown(result.failure);
      }
    } finally {
      this.working = false;
    }
  }
  async request(contact: string) {
    if (this.working || this.blocked()) return;
    const owner = this.capture();
    if (owner.sessionKind !== 'anonymous')
      throw new ApiFailure('invalid-request', 'not-sent');
    contactSchema.parse({ contact });
    const report = this.report(),
      serial = ++this.serial;
    this.working = true;
    this.set({
      phase: 'requesting',
      contact,
      challengeId: null,
      report,
      error: null,
      contactVerified: false,
    });
    try {
      const result = await this.auth.requestChallenge(contact);
      if (serial === this.serial && this.auth.isCurrent(owner))
        this.set({ phase: 'code', challengeId: result.challenge_id });
    } catch (e) {
      if (serial === this.serial) {
        const error = failure(e);
        this.set({ phase: 'email', error });
        this.cooldown(error);
      }
    } finally {
      if (serial === this.serial) this.working = false;
    }
  }
  editContact() {
    if (this.working) return;
    this.serial++;
    this.set({
      phase: 'email',
      challengeId: null,
      error: null,
      contactVerified: false,
    });
  }
  async verify(code: string) {
    if (
      this.working ||
      this.blocked() ||
      !this.state.challengeId ||
      this.state.phase !== 'code'
    )
      return;
    codeSchema.parse({ code });
    const before = this.capture();
    if (before.sessionKind !== 'anonymous') return;
    const contact = this.state.contact,
      challengeId = this.state.challengeId,
      report = this.report();
    const attempt = ++this.verifyAttempt;
    this.working = true;
    this.verifying = true;
    this.set({ phase: 'verifying', error: null });
    try {
      const result = await this.auth.change({
        kind: 'verify',
        challengeId,
        code,
      });
      const current = this.auth.getState();
      if (attempt !== this.verifyAttempt) return;
      if (
        result.resumeFromBoundaryId === before.boundaryId &&
        current.phase === 'ready' &&
        current.kind === 'customer'
      ) {
        this.owner = this.auth.capture();
        this.resumeGranted = true;
        this.set({
          ...empty(),
          phase: 'review',
          contact,
          report,
          contactVerified: true,
        });
      } else if (
        result.retryFromBoundaryId === before.boundaryId &&
        current.phase === 'ready' &&
        current.kind === 'anonymous'
      ) {
        this.owner = this.auth.capture();
        this.set({
          ...empty(),
          phase: 'code',
          contact,
          challengeId,
          report,
          error: result.failure,
        });
        if (result.failure) this.cooldown(result.failure);
      } else {
        this.owner = null;
        this.resumeGranted = false;
        this.set({
          ...empty(),
          phase: result.outcome === 'unknown' ? 'unknown' : 'unavailable',
          error: result.failure,
        });
      }
    } catch (e) {
      if (attempt === this.verifyAttempt) {
        this.owner = null;
        this.resumeGranted = false;
        this.set({ ...empty(), phase: 'unknown', error: failure(e) });
      }
    } finally {
      this.verifying = false;
      this.working = false;
    }
  }
  useExisting(contact: string) {
    const owner = this.capture();
    if (owner.sessionKind !== 'customer')
      throw new ApiFailure('invalid-request', 'not-sent');
    contactSchema.parse({ contact });
    const report = this.report();
    this.resumeGranted = true;
    this.set({
      phase: 'review',
      contact,
      report,
      contactVerified: false,
      continuedId: null,
      error: null,
    });
  }
  review(body: string) {
    if (this.state.phase === 'review' && !this.state.continuedId)
      this.set({ report: { body, service: this.state.report?.service ?? '' } });
  }
  async continue(language: 'en' | 'id') {
    if (
      this.working ||
      !this.resumeGranted ||
      this.state.phase !== 'review' ||
      !this.state.report
    )
      return null;
    const owner = this.capture();
    if (owner.sessionKind !== 'customer') return null;
    if (this.state.continuedId) return this.state.continuedId;
    const report = {
      body: this.state.report.body,
      service: this.state.report.service,
    };
    if (
      !report.body.trim() ||
      report.body.length > 3800 ||
      report.body.includes('\0')
    )
      return null;
    const serial = ++this.serial;
    this.working = true;
    this.set({ phase: 'continuing', error: null });
    try {
      const id = await this.chat.create(language);
      if (serial !== this.serial || !this.auth.isCurrent(owner)) return null;
      if (!id) {
        this.set({
          phase:
            this.chat.getState().phase === 'unknown-create'
              ? 'continue-unknown'
              : 'review',
          error: this.chat.getState().error,
        });
        return null;
      }
      this.chat.prefillReviewedReport(report);
      this.set({ phase: 'review', continuedId: id });
      return id;
    } catch (e) {
      if (serial === this.serial)
        this.set({ phase: 'review', error: failure(e) });
      return null;
    } finally {
      if (serial === this.serial) this.working = false;
    }
  }
  discard() {
    const source = this.chat.getState().meta?.conversation_id;
    if (
      source &&
      (source === this.originId || source === this.state.continuedId)
    )
      this.chat.discard();
    this.originId = null;
    this.serial++;
    this.verifyAttempt++;
    this.resumeGranted = false;
    this.held = null;
    const a = this.auth.getState();
    this.set({
      ...empty(),
      phase:
        a.phase === 'ready'
          ? a.kind === 'staff'
            ? 'staff'
            : 'email'
          : a.phase === 'guest'
            ? 'guest'
            : 'unavailable',
    });
  }
}
