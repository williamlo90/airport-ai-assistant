import { QueryClient } from '@tanstack/react-query';
import type { AuthCoordinator } from '../../app/auth/coordinator';
import type { BoundarySnapshot } from '../../api/contracts';
import { uuid } from '../../api/contracts';
import { ApiFailure } from '../../api/errors';
import type { ChatStore } from '../chat/store';
import { decodeConversation } from '../chat/contracts';
import { decodeCase } from './detail-contracts';
import { attachmentId, decodeUpload } from './attachment-contracts';
import {
  canUseDraft,
  decodeDraft,
  emptyForm,
  toForm,
  toPayload,
} from './contracts';
import type { DraftAction, DraftView, ReportForm } from './contracts';

type Uncertain =
  | { kind: 'create' | 'edit' | 'cancel'; version: number }
  | { kind: 'upload'; version: number; attachmentId: string | null };
interface Retained {
  handoffCaseId?: string | null;
  actionType: DraftAction;
  form: ReportForm;
  editing: boolean;
  uncertain: Uncertain | null;
  knownDraftId: string | null;
}
export interface CaseDraftState extends Retained {
  handoffCaseId: string | null;
  phase: 'idle' | 'loading' | 'intake' | 'review' | 'saving' | 'unavailable';
  draft: DraftView | null;
  error: ApiFailure | null;
  notice: 'changed' | 'saved' | null;
  localFilePending: boolean;
}
const empty = (): CaseDraftState => ({
  handoffCaseId: null,
  actionType: 'create_case',
  phase: 'idle',
  draft: null,
  error: null,
  notice: null,
  localFilePending: false,
  form: emptyForm(),
  editing: false,
  uncertain: null,
  knownDraftId: null,
});
const failure = (e: unknown) =>
  e instanceof ApiFailure ? e : new ApiFailure('invalid-response', 'unknown');
const retainedKey = (path: string) => 'case-draft:' + path;
export class CaseDraftStore {
  private state = empty();
  private listeners = new Set<() => void>();
  private serial = 0;
  private working = false;
  private uploadRetryAt = 0;
  private uploads = new Map<
    string,
    {
      attachmentId: string;
      version: number;
      boundaryId: string;
      principalId: string | null;
    }
  >();
  private owner: BoundarySnapshot | null = null;
  private selection: { kind: 'intake' | 'draft'; id: string } | null = null;
  private flight: {
    key: string;
    epoch: number;
    promise: Promise<void>;
  } | null = null;
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
    private chat: ChatStore,
    private scope: string,
  ) {}
  getState = () => this.state;
  subscribe = (fn: () => void) => {
    this.listeners.add(fn);
    return () => {
      this.listeners.delete(fn);
    };
  };
  private set(value: Partial<CaseDraftState>) {
    this.state = { ...this.state, ...value };
    this.listeners.forEach((fn) => fn());
  }
  connect() {
    const clear = () => {
      this.serial++;
      this.working = false;
      this.owner = null;
      this.flight = null;
      this.queries.clear();
      this.set(empty());
    };
    const off = this.auth.onReset(() => {
        if (this.auth.getState().phase !== 'checking') this.uploads.clear();
        this.uploadRetryAt = 0;
        clear();
      }),
      source = this.auth.onSourceUnavailable((error) => {
        clear();
        this.set({ phase: 'unavailable', error });
      });
    return () => {
      off();
      source();
    };
  }
  private path() {
    return this.selection
      ? (this.selection.kind === 'draft'
          ? '/action-drafts/'
          : '/conversations/') + this.selection.id
      : null;
  }
  private remember() {
    const key = this.path();
    if (key && this.owner && this.auth.isCurrent(this.owner))
      this.auth.keep(
        retainedKey(key),
        {
          handoffCaseId: this.state.handoffCaseId,
          actionType: this.state.actionType,
          form: this.state.form,
          editing: this.state.editing,
          uncertain: this.state.uncertain,
          knownDraftId: this.state.knownDraftId,
        } satisfies Retained,
        this.owner,
      );
  }
  async prepareFromChat(
    topic: string,
    contact: string,
    actionType: DraftAction = 'create_case',
    handoffCaseId: string | null = null,
  ) {
    const report = this.chat.plainReport(),
      meta = this.chat.getState().meta,
      owner = this.auth.capture();
    if (
      !meta ||
      owner.sessionKind === 'staff' ||
      (owner.sessionKind === 'anonymous' && actionType !== 'request_handoff') ||
      this.working
    )
      throw new ApiFailure('stale', 'not-sent');
    if (
      handoffCaseId !== null &&
      (!uuid(handoffCaseId) ||
        owner.sessionKind !== 'customer' ||
        actionType !== 'request_handoff')
    )
      throw new ApiFailure('invalid-request', 'not-sent');
    handoffCaseId = handoffCaseId?.toLowerCase() ?? null;
    if (
      owner.sessionKind === 'anonymous' &&
      (['booking', 'payment'].includes(topic) ||
        report.service === 'parking' ||
        report.service === 'lounge')
    )
      throw new ApiFailure('http', 'not-sent', 401, 'AUTH_REQUIRED');
    if (
      this.state.draft?.conversation_id === meta.conversation_id &&
      this.state.draft.state === 'pending' &&
      (this.state.draft.action_type === 'request_handoff' ||
        actionType === 'request_handoff')
    ) {
      if (
        this.state.draft.action_type !== actionType ||
        this.state.draft.case_id !== handoffCaseId
      )
        throw new ApiFailure('invalid-request', 'not-sent');
      return;
    }
    if (
      this.state.actionType !== actionType &&
      (this.state.knownDraftId || this.state.uncertain)
    )
      throw new ApiFailure('invalid-request', 'not-sent');
    if (
      this.selection?.kind === 'intake' &&
      this.selection.id === meta.conversation_id &&
      this.state.actionType === actionType &&
      this.state.handoffCaseId === handoffCaseId &&
      this.owner &&
      this.auth.isCurrent(this.owner)
    )
      return;
    const serial = ++this.serial;
    this.owner = owner;
    this.selection = { kind: 'intake', id: meta.conversation_id };
    this.working = true;
    this.set({ ...empty(), phase: 'loading' });
    try {
      if (handoffCaseId !== null) {
        const linked = await this.get(
          '/cases/' + handoffCaseId,
          decodeCase,
          owner,
          serial,
        );
        if (linked.id !== handoffCaseId)
          throw new ApiFailure('invalid-response', 'not-sent');
        if (linked.status === 'closed')
          throw new ApiFailure('http', 'not-sent', 409, 'CASE_CLOSED');
      }
      const path = this.path()!,
        fresh = await this.get(path, decodeConversation, owner, serial);
      if (fresh.conversation_id !== meta.conversation_id)
        throw new ApiFailure('invalid-response', 'rejected');
      if (this.serial !== serial || !this.auth.isCurrent(owner)) return;
      const retained = this.auth.retained<Retained>(retainedKey(path), owner);
      if (
        retained &&
        (retained.actionType !== actionType ||
          (retained.handoffCaseId ?? null) !== handoffCaseId) &&
        (retained.knownDraftId || retained.uncertain)
      )
        throw new ApiFailure('invalid-request', 'not-sent');
      const held =
        retained?.actionType === actionType &&
        (retained.handoffCaseId ?? null) === handoffCaseId
          ? retained
          : undefined;
      this.set({
        ...empty(),
        ...(held ?? {}),
        phase: 'intake',
        actionType,
        handoffCaseId,
        form: held?.form ?? {
          ...emptyForm(),
          category:
            topic === 'booking'
              ? 'booking_issue'
              : topic === 'payment'
                ? 'transaction_issue'
                : 'information',
          service_type:
            report.service === 'parking' || report.service === 'lounge'
              ? report.service
              : 'general',
          customer_report: report.body,
          summary:
            actionType === 'request_handoff' ? report.body.slice(0, 200) : '',
          contact_email:
            owner.sessionKind === 'anonymous' || handoffCaseId !== null
              ? ''
              : contact,
        },
      });
      this.remember();
    } catch (e) {
      if (this.serial === serial)
        this.set({ ...empty(), phase: 'unavailable', error: failure(e) });
    } finally {
      if (this.serial === serial) this.working = false;
    }
  }
  input(form: ReportForm) {
    if (
      this.working ||
      !this.owner ||
      !this.auth.isCurrent(this.owner) ||
      this.state.uncertain
    )
      return;
    this.set({
      form: this.state.handoffCaseId ? { ...form, contact_email: '' } : form,
    });
    this.remember();
  }
  beginEdit() {
    if (
      this.working ||
      this.state.uncertain ||
      !this.state.draft ||
      this.expired() ||
      this.state.draft.state !== 'pending'
    )
      return;
    this.auth.capture();
    this.set({ editing: true, error: null });
    this.remember();
  }
  stopEdit() {
    if (this.working) return;
    this.set({
      editing: false,
      form: this.state.draft
        ? toForm(this.state.draft.payload)
        : this.state.form,
    });
    this.remember();
  }
  expired(now = Date.now()) {
    return (
      !!this.state.draft &&
      (this.state.draft.state === 'expired' ||
        (this.state.draft.state === 'pending' &&
          Date.parse(this.state.draft.expires_at) <= now))
    );
  }
  private async get<T>(
    path: string,
    decode: (v: unknown) => T,
    owner: BoundarySnapshot,
    serial: number,
  ) {
    return this.queries.fetchQuery({
      queryKey: [
        this.scope,
        owner.boundaryId,
        owner.authEpoch,
        owner.sessionKind,
        owner.principalId,
        'preview',
        path,
      ],
      queryFn: ({ signal }) =>
        this.auth.readResource(
          {
            path,
            method: 'GET',
            boundary: owner,
            isRelevant: () => this.serial === serial,
            signal,
            successStatus: 200,
            decode,
          },
          'case-draft',
        ),
    });
  }
  enterIntake(): Promise<void> {
    const selected = this.selection;
    if (!selected || selected.kind !== 'intake') return Promise.resolve();
    if (
      this.state.phase === 'intake' &&
      this.owner &&
      this.auth.isCurrent(this.owner)
    )
      return Promise.resolve();
    return this.load('intake', selected.id);
  }
  open(id: string): Promise<void> {
    return this.load('draft', id);
  }
  private load(kind: 'intake' | 'draft', id: string): Promise<void> {
    if (!uuid(id)) return Promise.resolve();
    const owner = this.auth.capture(),
      key = kind + id;
    if (owner.sessionKind === 'staff') return Promise.resolve();
    if (this.flight?.key === key && this.flight.epoch === owner.authEpoch)
      return this.flight.promise;
    if (
      this.working &&
      this.selection?.kind === kind &&
      this.selection.id === id
    )
      return Promise.resolve();
    // A route change hides the old view immediately. Its already-dispatched
    // mutation remains uncertain in its own retention entry, never replayed.
    this.working = false;
    this.selection = { kind, id };
    this.owner = owner;
    const path = this.path()!,
      serial = ++this.serial;
    this.set({ ...empty(), phase: 'loading' });
    const promise = (async () => {
      try {
        if (kind === 'intake') {
          const meta = await this.get(path, decodeConversation, owner, serial);
          if (meta.conversation_id !== id)
            throw new ApiFailure('invalid-response', 'rejected');
          if (this.serial !== serial || !this.auth.isCurrent(owner)) return;
          const held = this.auth.retained<Retained>(retainedKey(path), owner);
          this.set({
            ...empty(),
            ...(held ?? {}),
            phase: held ? 'intake' : 'idle',
          });
        } else {
          const draft = await this.get(path, decodeDraft, owner, serial);
          if (draft.draft_id !== id)
            throw new ApiFailure('invalid-response', 'rejected');
          if (this.serial !== serial || !this.auth.isCurrent(owner)) return;
          const held = this.auth.retained<Retained>(retainedKey(path), owner);
          let uncertain = held?.uncertain ?? null;
          const upload = this.uploads.get(id);
          if (
            upload &&
            (upload.boundaryId !== owner.boundaryId ||
              upload.principalId !== owner.principalId ||
              owner.sessionKind !== 'customer')
          )
            this.uploads.delete(id);
          else if (upload)
            uncertain = {
              kind: 'upload',
              version: upload.version,
              attachmentId: upload.attachmentId,
            };
          if (
            uncertain &&
            (draft.state !== 'pending' ||
              (uncertain.kind === 'upload' &&
                uncertain.attachmentId !== null &&
                draft.payload.attachment_ids.includes(
                  uncertain.attachmentId,
                )) ||
              (uncertain.kind === 'edit' && draft.version > uncertain.version))
          ) {
            uncertain = null;
            this.uploads.delete(id);
          }
          this.set({
            phase: 'review',
            draft,
            actionType: draft.action_type,
            form: held?.form ?? toForm(draft.payload),
            editing: held?.editing ?? false,
            uncertain,
            knownDraftId: id,
            handoffCaseId: draft.case_id,
            error: null,
            notice:
              held?.uncertain ||
              (held &&
                JSON.stringify(held.form) !==
                  JSON.stringify(toForm(draft.payload)))
                ? 'changed'
                : null,
          });
        }
        this.remember();
      } catch (e) {
        if (this.serial === serial) {
          const error = failure(e);
          if (error.status === 403 || error.status === 404)
            this.auth.forgetRetained(retainedKey(path), owner);
          this.set({ ...empty(), phase: 'unavailable', error });
        }
      } finally {
        if (this.serial === serial) this.flight = null;
      }
    })();
    this.flight = { key, epoch: owner.authEpoch, promise };
    return promise;
  }
  async create(): Promise<string | null> {
    if (
      this.working ||
      this.state.phase !== 'intake' ||
      this.state.uncertain ||
      this.state.knownDraftId ||
      this.selection?.kind !== 'intake'
    )
      return null;
    const owner = this.auth.capture();
    const actionType = this.state.actionType;
    const payload = toPayload(
      this.state.form,
      actionType,
      owner.sessionKind === 'anonymous',
    );
    if (this.state.handoffCaseId) payload.contact_email = null;
    if (!canUseDraft(owner.sessionKind, actionType, payload)) return null;
    const conversation = this.selection.id,
      serial = ++this.serial,
      oldPath = this.path()!;
    this.working = true;
    this.owner = owner;
    this.set({
      phase: 'saving',
      error: null,
      uncertain: { kind: 'create', version: 0 },
    });
    this.remember();
    try {
      const draft = await this.auth.request({
        path: '/action-drafts',
        method: 'POST',
        body: {
          conversation_id: conversation,
          action_type: actionType,
          payload,
          ...(this.state.handoffCaseId
            ? { case_id: this.state.handoffCaseId }
            : {}),
        },
        boundary: owner,
        isRelevant: () => this.serial === serial,
        successStatus: 201,
        decode: decodeDraft,
      });
      if (this.serial !== serial || !this.auth.isCurrent(owner)) return null;
      if (
        draft.conversation_id !== conversation ||
        draft.action_type !== actionType ||
        draft.state !== 'pending' ||
        draft.case_id !== this.state.handoffCaseId
      )
        throw new ApiFailure('invalid-response', 'unknown');
      this.auth.keep(
        retainedKey(oldPath),
        {
          actionType,
          handoffCaseId: this.state.handoffCaseId,
          form: this.state.form,
          editing: false,
          uncertain: null,
          knownDraftId: draft.draft_id,
        } satisfies Retained,
        owner,
      );
      this.selection = { kind: 'draft', id: draft.draft_id };
      this.set({
        phase: 'review',
        draft,
        handoffCaseId: draft.case_id,
        form: toForm(draft.payload),
        editing: false,
        uncertain: null,
        knownDraftId: draft.draft_id,
      });
      this.remember();
      return draft.draft_id;
    } catch (e) {
      if (this.serial === serial) {
        const error = failure(e);
        if (error.status === 403 || error.status === 404) {
          this.auth.forgetRetained(retainedKey(oldPath), owner);
          this.set({ ...empty(), phase: 'unavailable', error });
          if (error.status === 403) await this.auth.recover();
        } else {
          this.set({
            phase: 'intake',
            error,
            uncertain:
              error.outcome === 'unknown'
                ? { kind: 'create', version: 0 }
                : null,
          });
          this.remember();
        }
      }
      return null;
    } finally {
      if (this.serial === serial) this.working = false;
    }
  }
  async update() {
    return this.mutate('edit');
  }
  fileSelection(pending: boolean) {
    if (this.working) return;
    this.set({ localFilePending: pending });
  }
  async removeAttachment(id: string) {
    const draft = this.state.draft;
    if (!draft?.payload.attachment_ids.includes(id) || this.state.editing)
      return;
    return this.mutate(
      'edit',
      draft.payload.attachment_ids.filter((value) => value !== id),
    );
  }
  async upload(file: Blob) {
    if (Date.now() < this.uploadRetryAt) return;
    const draft = this.state.draft;
    if (
      this.working ||
      this.state.phase !== 'review' ||
      this.state.editing ||
      this.state.uncertain ||
      !draft ||
      draft.state !== 'pending' ||
      draft.case_id !== null ||
      this.expired() ||
      draft.payload.attachment_ids.length >= 3
    )
      return;
    const owner = this.auth.capture();
    if (owner.sessionKind !== 'customer') return;
    if (
      !(file instanceof Blob) ||
      !file.size ||
      file.size > 5242880 ||
      !['image/png', 'image/jpeg', 'application/pdf'].includes(file.type)
    ) {
      this.set({
        error: new ApiFailure('http', 'rejected', 422, 'VALIDATION_ERROR'),
      });
      return;
    }
    const serial = ++this.serial,
      key = crypto.randomUUID();
    this.working = true;
    this.set({
      phase: 'saving',
      editing: false,
      error: null,
      uncertain: { kind: 'upload', version: draft.version, attachmentId: null },
    });
    let id: string | null = null;
    try {
      id = await attachmentId(draft.draft_id, key);
      if (serial !== this.serial || !this.auth.isCurrent(owner)) return;
      const command = {
        attachmentId: id,
        version: draft.version,
        boundaryId: owner.boundaryId,
        principalId: owner.principalId,
      };
      this.uploads.set(draft.draft_id, command);
      this.set({
        uncertain: { kind: 'upload', version: draft.version, attachmentId: id },
      });
      const result = await this.auth.request({
        path: `/attachments?draft_id=${draft.draft_id}&expected_version=${draft.version}`,
        method: 'POST',
        file,
        idempotencyKey: key,
        timeoutMs: 60000,
        boundary: owner,
        isRelevant: () => serial === this.serial,
        successStatus: 201,
        decode: decodeUpload,
      });
      if (serial !== this.serial || !this.auth.isCurrent(owner)) return;
      const next = result.draft,
        expected = {
          ...draft.payload,
          attachment_ids: [...draft.payload.attachment_ids, id],
        };
      if (
        result.attachment.id !== id ||
        result.attachment.mime_type !== file.type ||
        result.attachment.byte_size !== file.size ||
        next.draft_id !== draft.draft_id ||
        next.conversation_id !== draft.conversation_id ||
        next.case_id !== null ||
        next.action_type !== draft.action_type ||
        next.state !== 'pending' ||
        next.version !== draft.version + 1 ||
        next.expires_at !== draft.expires_at ||
        JSON.stringify(next.payload) !== JSON.stringify(expected)
      )
        throw new ApiFailure('invalid-response', 'unknown');
      this.uploads.delete(draft.draft_id);
      this.set({
        phase: 'review',
        draft: next,
        form: toForm(next.payload),
        uncertain: null,
        notice: 'saved',
      });
      this.remember();
    } catch (e) {
      if (serial !== this.serial) return;
      const error = failure(e);
      if (error.outcome !== 'unknown' || id === null)
        this.uploads.delete(draft.draft_id);
      if (error.status === 429)
        this.uploadRetryAt = Date.now() + (error.retryAfterMs ?? 1000);
      if (error.status === 403 || error.status === 404) {
        this.set({ ...empty(), phase: 'unavailable', error });
        return;
      }
      this.set({
        phase: 'review',
        error,
        uncertain:
          error.outcome === 'unknown' && id !== null
            ? { kind: 'upload', version: draft.version, attachmentId: id }
            : null,
      });
      this.remember();
    } finally {
      if (serial === this.serial) this.working = false;
    }
  }
  async cancel(intent?: {
    draftId: string;
    version: number;
    owner: BoundarySnapshot;
  }) {
    if (
      intent &&
      (!this.auth.isCurrent(intent.owner) ||
        this.state.draft?.draft_id !== intent.draftId ||
        this.state.draft.version !== intent.version)
    )
      return;
    return this.mutate('cancel');
  }
  private async mutate(kind: 'edit' | 'cancel', attachmentIds?: string[]) {
    const draft = this.state.draft;
    if (
      this.working ||
      !draft ||
      draft.state !== 'pending' ||
      this.expired() ||
      this.state.uncertain
    )
      return;
    const owner = this.auth.capture();
    const payload =
      kind === 'edit'
        ? toPayload(
            this.state.form,
            draft.action_type,
            owner.sessionKind === 'anonymous',
            attachmentIds ?? draft.payload.attachment_ids,
          )
        : null;
    if (payload && draft.case_id) payload.contact_email = null;
    if (
      !canUseDraft(
        owner.sessionKind,
        draft.action_type,
        payload ?? draft.payload,
        draft.case_id,
      )
    )
      return;
    const serial = ++this.serial,
      path = '/action-drafts/' + draft.draft_id;
    this.working = true;
    this.owner = owner;
    this.set({
      phase: 'saving',
      error: null,
      uncertain: { kind, version: draft.version },
    });
    this.remember();
    try {
      const next = await this.auth.request({
        path,
        method: kind === 'edit' ? 'PATCH' : 'DELETE',
        ...(payload
          ? { body: { expected_version: draft.version, payload } }
          : {}),
        boundary: owner,
        isRelevant: () => this.serial === serial,
        successStatus: 200,
        decode: decodeDraft,
      });
      if (this.serial !== serial || !this.auth.isCurrent(owner)) return;
      if (
        next.draft_id !== draft.draft_id ||
        next.action_type !== draft.action_type ||
        next.conversation_id !== draft.conversation_id ||
        next.case_id !== draft.case_id ||
        (kind === 'cancel'
          ? next.state !== 'cancelled'
          : next.state !== 'pending' ||
            next.version <= draft.version ||
            next.expires_at !== draft.expires_at)
      )
        throw new ApiFailure('invalid-response', 'unknown');
      this.set({
        phase: 'review',
        draft: next,
        form: toForm(next.payload),
        editing: false,
        uncertain: null,
        notice: 'saved',
      });
      this.remember();
    } catch (e) {
      if (this.serial !== serial) return;
      const error = failure(e);
      if (error.status === 404 || error.status === 403) {
        this.auth.forgetRetained(retainedKey(path), owner);
        this.set({ ...empty(), phase: 'unavailable', error });
        if (error.status === 403) await this.auth.recover();
        return;
      }
      this.set({
        phase: 'review',
        error,
        editing: error.outcome === 'unknown' ? false : this.state.editing,
        uncertain:
          error.outcome === 'unknown' ? { kind, version: draft.version } : null,
      });
      this.remember();
      if (error.status === 409 || error.status === 410) {
        this.working = false;
        const pending = this.open(draft.draft_id),
          stamp = this.serial;
        await pending;
        if (
          this.serial !== stamp ||
          !this.auth.isCurrent(owner) ||
          this.selection?.kind !== 'draft' ||
          this.selection.id !== draft.draft_id ||
          this.state.draft?.draft_id !== draft.draft_id
        )
          return;
        if (this.state.phase === 'review')
          this.set({ notice: 'changed', error, editing: false });
        this.remember();
      }
    } finally {
      if (this.serial === serial) this.working = false;
    }
  }
  async renew(): Promise<boolean> {
    const id = this.state.draft?.draft_id;
    if (!id || this.working || this.state.uncertain) return false;
    const originalOwner = this.auth.capture();
    const pending = this.open(id),
      stamp = this.serial;
    await pending;
    if (
      this.serial !== stamp ||
      !this.auth.isCurrent(originalOwner) ||
      this.selection?.kind !== 'draft' ||
      this.selection.id !== id ||
      this.state.draft?.draft_id !== id
    )
      return false;
    const draft = this.state.draft;
    if (!draft || !['expired', 'cancelled'].includes(draft.state)) return false;
    const owner = this.auth.capture();
    if (
      !canUseDraft(
        owner.sessionKind,
        draft.action_type,
        draft.payload,
        draft.case_id,
      )
    )
      return false;
    this.serial++;
    this.owner = owner;
    this.selection = { kind: 'intake', id: draft.conversation_id };
    this.set({
      ...empty(),
      phase: 'intake',
      actionType: draft.action_type,
      handoffCaseId: draft.case_id,
      form: toForm(draft.payload),
    });
    this.remember();
    return true;
  }
  discard() {
    const key = this.path();
    if (key && this.owner)
      this.auth.forgetRetained(retainedKey(key), this.owner);
    this.serial++;
    this.working = false;
    this.flight = null;
    this.selection = null;
    this.owner = null;
    this.queries.clear();
    this.set(empty());
  }
  dispose() {
    this.serial++;
    this.uploads.clear();
    this.queries.clear();
    this.listeners.clear();
  }
}
