import { ApiFailure } from '../../api/errors';
import type { StaffQueueStore } from './queue-store';
import {
  decodeAssignees,
  decodeMutation,
  decodeCandidates,
} from './action-contracts';
import type {
  AssigneePage,
  CloseReason,
  Candidate,
  StaffCommand,
  WorkStatus,
} from './action-contracts';
import type { BoundarySnapshot } from '../../api/contracts';
interface Draft {
  status: WorkStatus | '';
  nextAction: string;
  closeReason: CloseReason | '';
  resolution: string;
  closeNext: string;
  evidence: string;
  reference: string;
}
interface Retained {
  draft: Draft;
  member: string;
  memberVersion: number;
  review: boolean;
}
interface State extends Draft {
  phase: 'idle' | 'sending' | 'checking' | 'review';
  notice:
    | 'saved'
    | 'recorded-unavailable'
    | 'uncertain'
    | 'changed'
    | 'forbidden'
    | 'invalid-close'
    | 'invalid'
    | null;
  error: ApiFailure | null;
  directory: AssigneePage | null;
  directoryBusy: boolean;
  directoryError: ApiFailure | null;
  directoryPage: number;
  target: { membership_id: string; display_name: string | null } | null;
  confirmation: boolean;
  retryAt: number;
  closeConfirmation: boolean;
  candidate: Candidate | null;
  lookup: 'idle' | 'loading' | 'found' | 'empty' | 'error';
}
const empty = (): State => ({
  status: '',
  nextAction: '',
  closeReason: '',
  resolution: '',
  closeNext: '',
  evidence: '',
  reference: '',
  candidate: null,
  lookup: 'idle',
  closeConfirmation: false,
  phase: 'idle',
  notice: null,
  error: null,
  directory: null,
  directoryBusy: false,
  directoryError: null,
  directoryPage: 1,
  target: null,
  confirmation: false,
  retryAt: 0,
});
const failure = (e: unknown) =>
  e instanceof ApiFailure ? e : new ApiFailure('invalid-response', 'unknown');
/** One case-bound operation at a time. No command is retained or replayed. */
export class StaffActionStore {
  private state = empty();
  private listeners = new Set<() => void>();
  private serial = 0;
  private directorySerial = 0;
  private lookupSerial = 0;
  private lookupController: AbortController | null = null;
  private closeBasis = '';
  private disposed = false;
  private basisVersion = 0;
  private scope = '';
  private cursors: (string | null)[] = [null];
  private confirmationBasis = '';
  private retainedPath: string | null = null;
  private controller: AbortController | null = null;
  private directoryController: AbortController | null = null;
  constructor(
    readonly queue: StaffQueueStore,
    readonly caseId: string,
  ) {
    this.state.retryAt = queue.staff.readDeadline('action');
  }
  getState = () => this.state;
  subscribe = (f: () => void) => {
    this.listeners.add(f);
    return () => {
      this.listeners.delete(f);
    };
  };
  private set(v: Partial<State>) {
    this.state = { ...this.state, ...v };
    this.listeners.forEach((f) => f());
  }
  private basis() {
    const q = this.queue.getState(),
      scope = this.captureScope();
    if (
      !scope ||
      q.detailPhase !== 'ready' ||
      q.selectedId !== this.caseId ||
      q.detail?.id !== this.caseId ||
      q.capabilities?.case_id !== this.caseId ||
      q.capabilities.case_version !== q.detail.version ||
      q.capabilities.membership_id !== scope.membership.id ||
      q.capabilities.membership_version !== scope.membership.version ||
      q.capabilities.actor_principal_id !== scope.owner.principalId
    )
      return null;
    return { ...scope, detail: q.detail, cap: q.capabilities, key: q.scopeKey };
  }
  private path(member: string) {
    return '/staff/cases/' + this.caseId + '?membership_id=' + member;
  }
  private captureScope() {
    try {
      return this.queue.staff.captureScope();
    } catch {
      return null;
    }
  }
  private forgetRetained() {
    if (
      !this.retainedPath ||
      this.queue.staff.auth.getState().phase !== 'ready'
    )
      return;
    try {
      this.queue.staff.auth.forgetRetained(
        this.retainedPath,
        this.queue.staff.auth.capture(),
      );
    } catch {
      /* A rotated proof invalidates the boundary. */
    }
  }
  private fingerprint() {
    const b = this.basis();
    return b
      ? JSON.stringify([
          b.key,
          b.detail.version,
          b.cap.active_conversation,
          b.detail.assigned_membership_id,
        ])
      : '';
  }
  private hasDraft = () =>
    !!this.state.status ||
    !!this.state.nextAction ||
    !!this.state.closeReason ||
    !!this.state.resolution ||
    !!this.state.closeNext ||
    !!this.state.evidence ||
    !!this.state.reference;
  dirty = () =>
    !!this.state.closeReason ||
    !!this.state.resolution ||
    !!this.state.closeNext ||
    !!this.state.evidence ||
    !!this.state.reference ||
    this.state.lookup === 'loading' ||
    !!this.state.nextAction ||
    !!this.state.status ||
    !!this.state.target ||
    this.state.phase !== 'idle';
  private remember(review = false) {
    const b = this.basis();
    if (b) {
      this.retainedPath = this.path(b.membership.id);
      this.queue.staff.auth.keep(
        this.path(b.membership.id),
        {
          draft: {
            status: this.state.status,
            nextAction: this.state.nextAction,
            closeReason: this.state.closeReason,
            resolution: this.state.resolution,
            closeNext: this.state.closeNext,
            evidence: this.state.evidence,
            reference: this.state.reference,
          },
          member: b.membership.id,
          memberVersion: b.membership.version,
          review,
        } satisfies Retained,
        b.owner,
      );
    }
  }
  private sync = () => {
    const q = this.queue.getState();
    if (
      q.selectedId === this.caseId &&
      (q.detailError?.status === 403 ||
        q.detailError?.status === 404 ||
        q.error?.status === 403 ||
        q.error?.status === 404)
    ) {
      const scope = this.captureScope();
      if (scope)
        this.queue.staff.auth.forgetRetained(
          this.path(scope.membership.id),
          scope.owner,
        );
      this.serial++;
      this.directorySerial++;
      this.lookupSerial++;
      this.lookupController?.abort();
      this.closeBasis = '';
      this.directoryController?.abort();
      this.controller?.abort();
      this.basisVersion = 0;
      this.confirmationBasis = '';
      this.cursors = [null];
      this.set({
        ...empty(),
        retryAt: this.queue.staff.readDeadline('action'),
      });
      return;
    }
    if (
      this.scope &&
      (q.scopeKey !== this.scope || q.selectedId !== this.caseId)
    ) {
      if (
        this.retainedPath &&
        this.queue.staff.auth.getState().phase === 'ready'
      ) {
        this.forgetRetained();
        this.retainedPath = null;
      }
      this.serial++;
      this.directorySerial++;
      this.lookupSerial++;
      this.lookupController?.abort();
      this.closeBasis = '';
      this.directoryController?.abort();
      this.controller?.abort();
      this.state = empty();
      this.basisVersion = 0;
      this.scope = '';
      this.confirmationBasis = '';
      this.cursors = [null];
      this.set({});
    }
    const b = this.basis();
    if (!b || this.disposed) return;
    this.scope = b.key;
    this.retainedPath = this.path(b.membership.id);
    if (b.detail.status === 'closed') {
      this.forgetRetained();
      if (
        this.hasDraft() ||
        this.state.candidate ||
        this.state.closeConfirmation
      )
        this.set({
          status: '',
          nextAction: '',
          closeReason: '',
          resolution: '',
          closeNext: '',
          evidence: '',
          reference: '',
          candidate: null,
          closeConfirmation: false,
          confirmation: false,
          target: null,
          lookup: 'idle',
        });
      this.basisVersion = b.detail.version;
      return;
    }
    this.retainedPath = this.path(b.membership.id);
    if (this.basisVersion !== b.detail.version) {
      const old = this.basisVersion;
      this.basisVersion = b.detail.version;
      this.directorySerial++;
      this.lookupSerial++;
      this.lookupController?.abort();
      this.closeBasis = '';
      this.directoryController?.abort();
      this.cursors = [null];
      this.confirmationBasis = '';
      const held = this.queue.staff.auth.retained<Retained>(
        this.path(b.membership.id),
        b.owner,
      );
      const restore =
        held?.member === b.membership.id &&
        held.memberVersion === b.membership.version;
      this.set({
        directory: null,
        directoryBusy: false,
        directoryError: null,
        directoryPage: 1,
        target: null,
        confirmation: false,
        closeConfirmation: false,
        candidate: null,
        lookup: 'idle',
        ...(restore
          ? {
              ...held.draft,
              phase: held.review ? 'review' : 'idle',
              notice: held.review ? 'changed' : null,
            }
          : old && this.dirty()
            ? { phase: 'review', notice: 'changed' }
            : {}),
      });
    }
  };
  connect() {
    this.disposed = false;
    const off = this.queue.subscribe(this.sync);
    this.sync();
    return off;
  }
  edit(v: Partial<Draft>) {
    if (
      !this.basis() ||
      this.state.phase === 'sending' ||
      this.state.phase === 'checking'
    )
      return;
    this.closeBasis = '';
    if (v.reference !== undefined || v.closeReason !== undefined) {
      this.lookupSerial++;
      this.lookupController?.abort();
    }
    this.set({
      ...v,
      closeConfirmation: false,
      ...(v.reference !== undefined || v.closeReason !== undefined
        ? { candidate: null, lookup: 'idle' as const }
        : {}),
      error: null,
      notice: this.state.phase === 'review' ? this.state.notice : null,
    });
    this.remember(this.state.phase === 'review');
  }
  discard = () => {
    const b = this.basis();
    if (b)
      this.queue.staff.auth.forgetRetained(this.path(b.membership.id), b.owner);
    this.serial++;
    this.directorySerial++;
    this.lookupSerial++;
    this.lookupController?.abort();
    this.closeBasis = '';
    this.directoryController?.abort();
    this.controller?.abort();
    this.confirmationBasis = '';
    this.cursors = [null];
    this.set({ ...empty(), retryAt: this.queue.staff.readDeadline('action') });
  };
  review() {
    if (!this.basis() || this.state.phase !== 'review') return;
    this.set({
      phase: 'idle',
      notice: null,
      target: null,
      confirmation: false,
    });
    this.remember();
  }
  async directory(direction: 'first' | 'next' | 'previous' = 'first') {
    const b = this.basis();
    if (
      !b ||
      !b.cap.allowed_actions.includes('assign') ||
      this.state.directoryBusy ||
      this.state.phase !== 'idle' ||
      Date.now() < this.queue.staff.readDeadline('action')
    )
      return;
    if (direction === 'next') {
      if (!this.state.directory?.next_cursor) return;
      this.cursors.push(this.state.directory.next_cursor);
    } else if (direction === 'previous') {
      if (this.cursors.length < 2) return;
      this.cursors.pop();
    } else this.cursors = [null];
    const cursor = this.cursors.at(-1) ?? null,
      serial = ++this.directorySerial,
      fingerprint = this.fingerprint();
    this.directoryController?.abort();
    this.directoryController = new AbortController();
    this.confirmationBasis = '';
    this.set({
      directory: null,
      directoryBusy: true,
      directoryError: null,
      target: null,
      confirmation: false,
    });
    try {
      const params = new URLSearchParams({
        membership_id: b.membership.id,
        limit: '25',
      });
      if (cursor) params.set('cursor', cursor);
      const result = await this.queue.staff.auth.request({
        method: 'GET',
        path: '/staff/cases/' + this.caseId + '/eligible-assignees?' + params,
        boundary: b.owner,
        signal: this.directoryController.signal,
        isRelevant: () =>
          !this.disposed &&
          serial === this.directorySerial &&
          fingerprint === this.fingerprint(),
        successStatus: 200,
        decode: (v) => decodeAssignees(v, b.cap, cursor),
      });
      if (
        !this.disposed &&
        serial === this.directorySerial &&
        fingerprint === this.fingerprint()
      )
        this.set({ directory: result, directoryPage: this.cursors.length });
    } catch (e) {
      if (!this.disposed && serial === this.directorySerial) {
        const error = failure(e);
        this.wait(error);
        this.set({ directoryError: error });
        if (error.status === 403 || error.status === 404) {
          await this.queue.openCase(this.caseId, true);
          if (!this.disposed && serial === this.directorySerial)
            this.set({ phase: 'review', notice: 'changed' });
        }
      }
    } finally {
      if (!this.disposed && serial === this.directorySerial)
        this.set({ directoryBusy: false });
    }
  }
  choose(id: string) {
    if (this.state.phase !== 'idle') return;
    const target = this.state.directory?.items.find(
      (v) => v.membership_id === id,
    );
    if (target) this.set({ target, confirmation: false });
  }
  requestAssignment() {
    const b = this.basis();
    if (
      !b ||
      this.state.phase !== 'idle' ||
      !b.cap.allowed_actions.includes('assign') ||
      !this.state.target ||
      this.state.target.membership_id === b.detail.assigned_membership_id
    )
      return;
    this.confirmationBasis = this.fingerprint();
    this.set({ confirmation: true });
  }
  requestUnassign() {
    const b = this.basis();
    if (
      !b ||
      this.state.phase !== 'idle' ||
      !b.cap.allowed_actions.includes('unassign')
    )
      return;
    this.confirmationBasis = this.fingerprint();
    this.set({ target: null, confirmation: true });
  }
  cancelConfirmation() {
    this.confirmationBasis = '';
    this.set({ confirmation: false });
  }
  async confirmAssignment() {
    if (
      !this.state.confirmation ||
      !this.confirmationBasis ||
      this.confirmationBasis !== this.fingerprint()
    ) {
      this.cancelConfirmation();
      return;
    }
    await this.send({
      kind: 'assignment',
      target: this.state.target?.membership_id ?? null,
    });
  }
  async claim() {
    const b = this.basis();
    if (
      !b ||
      !b.cap.allowed_actions.includes('claim') ||
      b.cap.self_claim_membership_id !== b.membership.id ||
      b.detail.assigned_membership_id !== null ||
      b.cap.active_conversation
    )
      return;
    await this.send({
      kind: 'assignment',
      target: b.cap.self_claim_membership_id,
    });
  }
  async status() {
    const b = this.basis(),
      { status, nextAction } = this.state;
    if (
      !b ||
      !status ||
      !b.cap.allowed_actions.includes('update_status') ||
      !b.cap.allowed_statuses.includes(status) ||
      !nextAction.trim() ||
      [...nextAction].length > 2000 ||
      nextAction.includes('\0')
    ) {
      this.set({ notice: 'invalid' });
      return;
    }
    await this.send({ kind: 'status', status, nextAction });
  }
  async findDuplicate() {
    const b = this.basis(),
      reference = this.state.reference;
    if (
      !b ||
      this.state.phase !== 'idle' ||
      this.state.lookup === 'loading' ||
      this.state.closeReason !== 'duplicate' ||
      !b.cap.allowed_actions.includes('close') ||
      !b.cap.allowed_close_reasons.includes('duplicate') ||
      Date.now() < this.queue.staff.readDeadline('action')
    )
      return;
    this.set({ candidate: null, closeConfirmation: false });
    this.closeBasis = '';
    if (!/^CASE-[a-f0-9]{32}$/.test(reference)) {
      this.set({ notice: 'invalid-close', lookup: 'idle' });
      return;
    }
    const serial = ++this.lookupSerial,
      fingerprint = this.fingerprint();
    this.lookupController?.abort();
    this.lookupController = new AbortController();
    this.set({ lookup: 'loading', notice: null });
    try {
      const candidate = await this.queue.staff.auth.request({
        method: 'POST',
        path: '/staff/cases/' + this.caseId + '/duplicate-candidates/search',
        body: {
          membership_id: b.membership.id,
          expected_version: b.detail.version,
          public_reference: reference,
        },
        boundary: b.owner,
        signal: this.lookupController.signal,
        isRelevant: () =>
          !this.disposed &&
          serial === this.lookupSerial &&
          fingerprint === this.fingerprint(),
        successStatus: 200,
        decode: (v) => decodeCandidates(v, b.cap, reference),
      });
      if (
        !this.disposed &&
        serial === this.lookupSerial &&
        fingerprint === this.fingerprint()
      )
        this.set({ candidate, lookup: candidate ? 'found' : 'empty' });
    } catch (e) {
      if (this.disposed || serial !== this.lookupSerial) return;
      const error = failure(e);
      this.wait(error);
      this.set({ lookup: 'error', candidate: null });
      if ([403, 404, 409].includes(error.status ?? 0)) {
        this.remember(true);
        if (error.status === 403) {
          this.set({
            ...empty(),
            phase: 'checking',
            notice: 'forbidden',
            retryAt: this.state.retryAt,
          });
          await this.queue.staff.auth.recheck();
        } else await this.queue.openCase(this.caseId, true);
        if (!this.disposed && serial === this.lookupSerial)
          this.set({ phase: 'review', notice: 'changed' });
      }
    }
  }
  requestClose() {
    const b = this.basis(),
      s = this.state;
    const text = (v: string, max: number) =>
      !!v.trim() && [...v].length <= max && !v.includes('\0');
    if (
      !b ||
      s.phase !== 'idle' ||
      s.lookup === 'loading' ||
      Date.now() < this.queue.staff.readDeadline('action')
    )
      return;
    if (
      !s.closeReason ||
      !b.cap.allowed_actions.includes('close') ||
      !b.cap.allowed_close_reasons.includes(s.closeReason) ||
      !text(s.resolution, 4000) ||
      !text(s.closeNext, 2000) ||
      (s.closeReason === 'no_response' && !text(s.evidence, 2000)) ||
      (s.closeReason === 'duplicate' &&
        (!s.candidate ||
          s.lookup !== 'found' ||
          s.candidate.public_reference !== s.reference))
    ) {
      this.set({ notice: 'invalid-close' });
      return;
    }
    this.closeBasis = this.fingerprint();
    this.set({ closeConfirmation: true, confirmation: false, notice: null });
  }
  cancelClose() {
    this.closeBasis = '';
    this.set({ closeConfirmation: false });
  }
  async confirmClose() {
    const b = this.basis(),
      s = this.state;
    if (
      !b ||
      !s.closeConfirmation ||
      !s.closeReason ||
      !this.closeBasis ||
      this.closeBasis !== this.fingerprint() ||
      !b.cap.allowed_actions.includes('close') ||
      !b.cap.allowed_close_reasons.includes(s.closeReason)
    ) {
      this.cancelClose();
      return;
    }
    await this.send({
      kind: 'close',
      reason: s.closeReason,
      resolution: s.resolution,
      nextAction: s.closeNext,
      candidate: s.closeReason === 'duplicate' ? s.candidate : null,
      evidence: s.closeReason === 'no_response' ? s.evidence : '',
    });
  }
  private wait(error: ApiFailure) {
    if (error.status === 429) {
      const until = Date.now() + (error.retryAfterMs ?? 0);
      this.queue.staff.waitForReads('action', until);
      this.set({ retryAt: this.queue.staff.readDeadline('action') });
    }
  }
  private relevant(owner: BoundarySnapshot, key: string, serial: number) {
    return (
      !this.disposed &&
      serial === this.serial &&
      this.queue.staff.auth.isCurrent(owner) &&
      this.queue.getState().scopeKey === key &&
      this.queue.getState().selectedId === this.caseId
    );
  }
  private async send(command: StaffCommand) {
    const b = this.basis();
    if (
      !b ||
      this.state.phase !== 'idle' ||
      Date.now() < this.queue.staff.readDeadline('action')
    )
      return;
    const claim =
      command.kind === 'assignment' && b.cap.allowed_actions.includes('claim');
    if (
      command.kind === 'assignment' &&
      !claim &&
      !(command.target === null
        ? b.cap.allowed_actions.includes('unassign')
        : b.cap.allowed_actions.includes('assign') &&
          this.state.directory?.items.some(
            (v) => v.membership_id === command.target,
          ))
    )
      return;
    const serial = ++this.serial,
      owner = b.owner,
      key = b.key;
    const body = Object.freeze({
      expected_version: b.detail.version,
      ...(b.cap.active_conversation
        ? { expected_conversation_version: b.cap.active_conversation.version }
        : {}),
      ...(command.kind === 'assignment'
        ? { assignee_id: command.target }
        : command.kind === 'close'
          ? {
              status: 'closed',
              next_action: command.nextAction,
              close_reason: command.reason,
              resolution: command.resolution,
              ...(command.reason === 'duplicate'
                ? { related_case_id: command.candidate?.case_id }
                : {}),
              ...(command.reason === 'no_response'
                ? { followup_evidence: command.evidence }
                : {}),
            }
          : { status: command.status, next_action: command.nextAction }),
    });
    this.remember(true);
    this.confirmationBasis = '';
    this.controller = new AbortController();
    this.set({
      phase: 'sending',
      notice: null,
      error: null,
      confirmation: false,
      closeConfirmation: false,
      candidate: null,
      lookup: 'idle',
    });
    this.lookupSerial++;
    this.lookupController?.abort();
    this.closeBasis = '';
    let result: 'saved' | 'uncertain' | 'changed' | 'forbidden' = 'saved';
    try {
      await this.queue.staff.auth.request({
        method: 'PATCH',
        path:
          '/staff/cases/' +
          this.caseId +
          '/' +
          (command.kind === 'close' ? 'status' : command.kind),
        body,
        boundary: owner,
        signal: this.controller.signal,
        isRelevant: () => this.relevant(owner, key, serial),
        successStatus: 200,
        decode: (v) => decodeMutation(v, b.detail, command, claim),
      });
      if (!this.relevant(owner, key, serial)) return;
      if (command.kind === 'close') {
        this.queue.staff.auth.forgetRetained(this.path(b.membership.id), owner);
        this.set({ ...empty(), phase: 'sending', retryAt: this.state.retryAt });
      } else {
        this.set({
          target: null,
          ...(command.kind === 'status'
            ? { status: '' as const, nextAction: '' }
            : {}),
        });
        if (this.hasDraft()) this.remember(true);
        else
          this.queue.staff.auth.forgetRetained(
            this.path(b.membership.id),
            owner,
          );
      }
    } catch (e) {
      if (!this.relevant(owner, key, serial)) return;
      const error = failure(e);
      this.wait(error);
      this.set({ error });
      result =
        error.status === 403
          ? 'forbidden'
          : error.outcome === 'unknown'
            ? 'uncertain'
            : 'changed';
      if (error.status === 403) {
        this.set({
          ...empty(),
          phase: 'checking',
          retryAt: this.state.retryAt,
          status: '',
          nextAction: '',
          target: null,
          directory: null,
          notice: 'forbidden',
        });
        await this.queue.staff.auth.recheck(); // Same-proof draft may restore only after fresh scoped reads.
        return;
      }
      if (error.status === 404) {
        this.queue.staff.auth.forgetRetained(this.path(b.membership.id), owner);
        this.set({
          ...empty(),
          phase: 'checking',
          retryAt: this.state.retryAt,
        });
      }
    }
    if (!this.relevant(owner, key, serial)) return;
    this.set({
      phase: 'checking',
      notice: result,
      directory: null,
      target: null,
      candidate: null,
      lookup: 'idle',
      closeConfirmation: false,
    });
    await this.queue.loadQueue(true);
    if (!this.relevant(owner, key, serial)) return;
    if (this.queue.getState().phase !== 'ready') {
      this.set({
        phase: 'review',
        notice: result === 'saved' ? 'recorded-unavailable' : result,
      });
      return;
    }
    await this.queue.openCase(this.caseId, true);
    if (!this.relevant(owner, key, serial)) return;
    const ready = !!this.basis();
    const draftNeedsReview = command.kind !== 'close' && this.hasDraft();
    this.set({
      phase:
        result === 'saved' && ready && !draftNeedsReview ? 'idle' : 'review',
      notice: result === 'saved' && !ready ? 'recorded-unavailable' : result,
    });
  }
  dispose() {
    if (
      this.retainedPath &&
      this.queue.staff.auth.getState().phase === 'ready'
    ) {
      this.forgetRetained();
    }
    this.disposed = true;
    this.serial++;
    this.directorySerial++;
    this.lookupSerial++;
    this.lookupController?.abort();
    this.closeBasis = '';
    this.directoryController?.abort();
    this.controller?.abort();
    this.state = empty();
    this.listeners.clear();
  }
}
