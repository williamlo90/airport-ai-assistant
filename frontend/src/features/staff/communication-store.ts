import { ApiFailure } from '../../api/errors';
import type { Decoder } from '../../api/contracts';
import type { StaffQueueStore } from './queue-store';
import { canHandleCases } from './store';
import {
  decodeStaffChat,
  decodeChatAcceptance,
  decodeStaffReceipt,
  decodeStaffTranslation,
  decodeStaffCorrespondence,
  decodeCommunicationCapability,
  type StaffChat,
  type StaffTranslation,
  type StaffCorrespondencePage,
  type CommunicationCapability,
} from './communication-contracts';

type Channel = 'chat' | 'email';
interface Pending {
  kind: 'accept' | 'send' | 'translate' | 'approve';
  key: string;
  conversationId: string | null;
  channel: Channel;
  draftId: string | null;
}
interface Tombstone {
  binding: string;
  pending: Pending;
}
// Source outages purge private caches, but must not erase an already-issued
// operation's safe recovery IDs. No text, command, cookie or web storage here.
const tombstones = new WeakMap<
  StaffQueueStore['staff']['auth'],
  Map<string, Tombstone>
>();
const tombstoneResetRegistered = new WeakSet<
  StaffQueueStore['staff']['auth']
>();
interface State {
  available: boolean;
  phase: 'idle' | 'loading' | 'sending' | 'checking' | 'unknown';
  chat: StaffChat | null;
  capability: CommunicationCapability | null;
  history: StaffCorrespondencePage | null;
  historyPage: number;
  body: string;
  language: 'en' | 'id';
  channel: Channel;
  draft: StaffTranslation | null;
  reviewed: string;
  pending: Pending | null;
  notice:
    'saved' | 'accepted' | 'translation-ready' | 'unknown' | 'changed' | null;
  error: ApiFailure | null;
  retryAt: number;
}
const empty = (): State => ({
  available: false,
  phase: 'idle',
  chat: null,
  capability: null,
  history: null,
  historyPage: 1,
  body: '',
  language: 'en',
  channel: 'email',
  draft: null,
  reviewed: '',
  pending: null,
  notice: null,
  error: null,
  retryAt: 0,
});
const failure = (e: unknown) =>
  e instanceof ApiFailure ? e : new ApiFailure('invalid-response', 'unknown');
type Position = { time: string; id: string } | null;
const queryValue = (s: string) => encodeURIComponent(s).replaceAll('.', '%2E');

/** One human command at a time. Unknown outcomes use GET only; no POST replay. */
export class StaffCommunicationStore {
  private state = empty();
  private listeners = new Set<() => void>();
  private serial = 0;
  private key = '';
  private version = 0;
  private connected = false;
  private controller: AbortController | null = null;
  private positions: Position[] = [null];
  constructor(
    readonly queue: StaffQueueStore,
    readonly caseId: string,
  ) {}
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
  private basis() {
    const q = this.queue.getState();
    let scope: ReturnType<StaffQueueStore['staff']['captureScope']>;
    try {
      scope = this.queue.staff.captureScope();
    } catch {
      return null;
    }
    const cap = q.capabilities,
      detail = q.detail;
    if (
      !scope ||
      !canHandleCases(scope.membership) ||
      !q.scopeKey ||
      q.phase !== 'ready' ||
      q.detailPhase !== 'ready' ||
      q.selectedId !== this.caseId ||
      detail?.id !== this.caseId ||
      cap?.case_id !== this.caseId ||
      cap.case_version !== detail.version ||
      cap.membership_id !== scope.membership.id ||
      cap.membership_version !== scope.membership.version ||
      cap.actor_principal_id !== scope.owner.principalId ||
      cap.organization.id !== scope.membership.organization_id ||
      !scope.membership.queue_ids.includes(cap.queue.id)
    )
      return null;
    return {
      ...scope,
      cap,
      detail,
      key: JSON.stringify([
        q.scopeKey,
        this.caseId,
        scope.membership.id,
        scope.membership.version,
      ]),
    };
  }
  private retainedPath(member: string) {
    return `staff-communication:${this.caseId}:${member}`;
  }
  private tombstoneMap() {
    let map = tombstones.get(this.queue.staff.auth);
    if (!map) {
      map = new Map();
      tombstones.set(this.queue.staff.auth, map);
    }
    const auth = this.queue.staff.auth;
    if (!tombstoneResetRegistered.has(auth)) {
      tombstoneResetRegistered.add(auth);
      // Owned by the auth boundary lifetime, including when no case pane is mounted.
      auth.onReset(() => tombstones.delete(auth));
    }
    return map;
  }
  private binding(
    b: NonNullable<ReturnType<StaffCommunicationStore['basis']>>,
  ) {
    return JSON.stringify([
      b.key,
      b.owner.boundaryId,
      b.owner.authEpoch,
      b.owner.principalId,
    ]);
  }
  private cancel() {
    this.serial++;
    this.controller?.abort();
    this.controller = null;
  }
  private sync = () => {
    const b = this.basis(),
      key = b?.key ?? '';
    if (key === this.key && (!b || this.version === b.detail.version)) return;
    this.cancel();
    this.key = key;
    this.version = b?.detail.version ?? 0;
    this.positions = [null];
    const marker = b
      ? this.tombstoneMap().get(this.retainedPath(b.membership.id))
      : null;
    const pending =
      b && marker?.binding === this.binding(b) ? marker.pending : null;
    this.set({
      ...empty(),
      available: !!b,
      pending,
      channel: pending?.channel ?? 'email',
      phase: pending ? 'unknown' : 'idle',
      notice: pending ? 'unknown' : null,
      retryAt: this.queue.staff.readDeadline('action'),
    });
  };
  connect() {
    this.connected = true;
    const off = this.queue.subscribe(this.sync),
      staff = this.queue.staff.subscribe(this.sync);
    const clear = () => {
      this.cancel();
      this.key = '';
      this.version = 0;
      this.positions = [null];
      this.set(empty());
    };
    const reset = this.queue.staff.auth.onReset(() => {
        tombstones.delete(this.queue.staff.auth);
        clear();
      }),
      source = this.queue.staff.auth.onSourceUnavailable(clear);
    this.sync();
    return () => {
      this.connected = false;
      off();
      staff();
      reset();
      source();
      clear();
    };
  }
  private busy() {
    return ['loading', 'sending', 'checking'].includes(this.state.phase);
  }
  dirty = () =>
    !!this.state.body ||
    !!this.state.draft ||
    !!this.state.pending ||
    this.state.phase === 'sending';
  discard = () => {
    if (this.state.phase === 'sending' || this.state.phase === 'checking')
      return;
    // Navigation may discard unsent text, never silently resolve an unknown send.
    const pending = this.state.pending;
    this.set({
      body: '',
      draft: null,
      reviewed: '',
      pending,
      phase: pending ? 'unknown' : 'idle',
      error: null,
      notice: pending ? 'unknown' : null,
    });
  };
  edit(body: string) {
    if (
      this.state.available &&
      this.basis() &&
      !this.busy() &&
      !this.state.pending &&
      !this.state.draft
    )
      this.set({ body, notice: null, error: null });
  }
  configure(language: 'en' | 'id', channel: Channel) {
    if (
      this.state.available &&
      this.basis() &&
      !this.busy() &&
      !this.state.pending &&
      !this.state.draft
    )
      this.set({ language, channel, notice: null, error: null });
  }
  review(text: string) {
    if (
      !this.busy() &&
      !this.state.pending &&
      this.state.draft?.state === 'ready'
    )
      this.set({ reviewed: text });
  }
  canReply(channel = this.state.channel) {
    const b = this.basis(),
      chat = this.state.chat;
    if (
      !b ||
      !this.state.available ||
      this.busy() ||
      this.state.pending ||
      b.detail.status === 'closed' ||
      b.detail.assigned_membership_id !== b.membership.id
    )
      return false;
    const active = b.cap.active_conversation;
    if (
      active &&
      (!chat ||
        chat.conversation_id !== active.id ||
        chat.case_version !== b.detail.version ||
        chat.mode !== 'human' ||
        chat.handler_membership_id !== b.membership.id)
    )
      return false;
    return channel === 'chat'
      ? !!active
      : this.state.capability?.email_available === true;
  }
  private current(
    b: NonNullable<ReturnType<StaffCommunicationStore['basis']>>,
    serial: number,
  ) {
    const current = this.basis();
    return (
      this.connected &&
      this.serial === serial &&
      current?.key === b.key &&
      current.detail.version === b.detail.version &&
      this.queue.staff.auth.isCurrent(b.owner)
    );
  }
  private async read<T>(
    b: NonNullable<ReturnType<StaffCommunicationStore['basis']>>,
    serial: number,
    path: string,
    decode: Decoder<T>,
  ) {
    if (!this.controller || !this.current(b, serial))
      throw new ApiFailure('stale', 'not-sent');
    return this.queue.staff.auth.readResource({
      path,
      method: 'GET',
      boundary: b.owner,
      signal: this.controller!.signal,
      successStatus: 200,
      decode,
      isRelevant: () => this.current(b, serial),
    });
  }
  private readFailure(e: unknown) {
    const error = failure(e);
    let retryAt = this.state.retryAt;
    if (error.status === 429) {
      retryAt = Math.max(retryAt, Date.now() + (error.retryAfterMs ?? 5000));
      this.queue.staff.waitForReads('action', retryAt);
    }
    const denied =
      error.status === 401 ||
      error.status === 403 ||
      error.status === 404 ||
      error.kind === 'stale' ||
      error.kind === 'invalid-response';
    this.set({
      error,
      retryAt,
      phase: this.state.pending ? 'unknown' : 'idle',
      ...(error.status === 409 || error.kind === 'stale'
        ? { available: false, notice: 'changed' as const }
        : {}),
      ...(denied
        ? {
            available: false,
            body: '',
            draft: null,
            reviewed: '',
            chat: null,
            history: null,
            capability: null,
          }
        : {}),
    });
  }
  async load(position: Position = null, page = 1) {
    this.sync();
    const b = this.basis();
    if (
      !this.connected ||
      !b ||
      this.busy() ||
      Date.now() < this.queue.staff.readDeadline('action')
    )
      return;
    this.cancel();
    const serial = this.serial;
    this.controller = new AbortController();
    this.set({ phase: 'loading', error: null, history: null });
    try {
      const capability = await this.read(
        b,
        serial,
        `/staff/cases/${this.caseId}/communication-capabilities?membership_id=${b.membership.id}`,
        (v) => decodeCommunicationCapability(v, this.caseId, b.membership.id),
      );
      if (!this.current(b, serial)) return;
      if (capability.case_version !== b.detail.version)
        throw new ApiFailure('stale', 'rejected');
      const cid = b.cap.active_conversation?.id;
      const chat = cid
        ? await this.read(b, serial, `/staff/conversations/${cid}`, (v) =>
            decodeStaffChat(v, this.caseId, cid),
          )
        : null;
      if (!this.current(b, serial)) return;
      let path = `/staff/cases/${this.caseId}/messages?membership_id=${b.membership.id}&limit=50`;
      if (position)
        path += `&before_time=${queryValue(position.time)}&before_id=${position.id}`;
      const history = await this.read(b, serial, path, (v) =>
        decodeStaffCorrespondence(v, this.caseId),
      );
      if (!this.current(b, serial)) return;
      this.set({
        available: true,
        chat,
        capability,
        history,
        historyPage: page,
        ...(!this.state.capability &&
        !this.state.body &&
        !this.state.draft &&
        !this.state.pending
          ? { channel: cid ? ('chat' as const) : ('email' as const) }
          : {}),
        phase: this.state.pending ? 'unknown' : 'idle',
      });
    } catch (e) {
      if (this.current(b, serial)) this.readFailure(e);
    }
  }
  async older() {
    const h = this.state.history;
    if (!h?.has_more || !h.before_id || !h.before_time || this.busy()) return;
    const next = { id: h.before_id, time: h.before_time };
    this.positions[this.state.historyPage] = next;
    await this.load(next, this.state.historyPage + 1);
  }
  async newer() {
    if (this.state.historyPage > 1 && !this.busy())
      await this.load(
        this.positions[this.state.historyPage - 2] ?? null,
        this.state.historyPage - 1,
      );
  }
  private remember(
    pending: Pending,
    b: NonNullable<ReturnType<StaffCommunicationStore['basis']>>,
  ) {
    this.tombstoneMap().set(this.retainedPath(b.membership.id), {
      binding: this.binding(b),
      pending: { ...pending },
    });
  }
  private complete(
    b: NonNullable<ReturnType<StaffCommunicationStore['basis']>>,
  ) {
    this.tombstoneMap().delete(this.retainedPath(b.membership.id));
    this.set({ pending: null, phase: 'idle', error: null });
  }
  private async perform<T>(
    pending: Pending,
    path: string,
    body: unknown,
    decode: Decoder<T>,
    apply: (value: T) => void,
  ) {
    const b = this.basis();
    if (
      !this.connected ||
      !b ||
      this.busy() ||
      this.state.pending ||
      Date.now() < this.queue.staff.readDeadline('action')
    )
      return false;
    this.cancel();
    const serial = this.serial;
    this.controller = new AbortController();
    this.remember(pending, b);
    this.set({ pending, phase: 'sending', notice: null, error: null });
    try {
      const result = await this.queue.staff.auth.request({
        path,
        method: 'POST',
        body,
        boundary: b.owner,
        idempotencyKey: pending.key,
        signal: this.controller.signal,
        timeoutMs: pending.kind === 'translate' ? 110000 : 15000,
        successStatus: pending.kind === 'accept' ? 200 : 201,
        decode,
        isRelevant: () => this.current(b, serial),
      });
      if (!this.current(b, serial)) return false;
      apply(result);
      this.complete(b);
      return true;
    } catch (e) {
      if (!this.current(b, serial)) return false;
      const error = failure(e);
      if (
        error.outcome === 'unknown' ||
        error.code === 'AGENT_TRANSLATION_IN_FLIGHT'
      )
        this.set({ phase: 'unknown', error, notice: 'unknown' });
      else {
        this.complete(b);
        this.readFailure(error);
      }
      return false;
    }
  }
  private async refreshCase() {
    const b = this.basis(),
      notice = this.state.notice,
      language = this.state.language,
      channel = this.state.channel;
    await this.queue.openCase(this.caseId, true);
    if (!this.connected) return;
    this.sync();
    await this.load();
    if (
      b &&
      this.basis()?.key === b.key &&
      this.queue.staff.auth.isCurrent(b.owner)
    )
      this.set({ notice, language, channel });
  }
  async accept() {
    const b = this.basis(),
      chat = this.state.chat;
    if (
      !b ||
      !chat ||
      chat.mode !== 'waiting_staff' ||
      chat.case_version !== b.detail.version
    )
      return;
    const pending: Pending = {
      kind: 'accept',
      key: crypto.randomUUID(),
      conversationId: chat.conversation_id,
      channel: 'chat',
      draftId: null,
    };
    if (
      await this.perform(
        pending,
        `/staff/conversations/${chat.conversation_id}/accept`,
        {
          expected_version: chat.version,
          expected_case_version: b.detail.version,
        },
        (v) => {
          const r = decodeChatAcceptance(v, this.caseId, chat.conversation_id);
          if (r.handler_membership_id !== b.membership.id)
            throw new ApiFailure('invalid-response', 'unknown');
          return r;
        },
        (r) => this.set({ chat: r, notice: 'accepted' }),
      )
    )
      await this.refreshCase();
  }
  private replyCommand() {
    const b = this.basis();
    if (
      !b ||
      !this.canReply() ||
      !this.state.body.trim() ||
      [...this.state.body].length > 4000 ||
      this.state.body.includes('\0')
    )
      return null;
    const cid = b.cap.active_conversation?.id ?? null;
    return {
      b,
      cid,
      body: cid
        ? {
            conversation_id: cid,
            channel: this.state.channel,
            body: this.state.body,
            language: this.state.language,
            expected_version: b.detail.version,
            expected_conversation_version: this.state.chat!.version,
            expected_epoch: this.state.chat!.generation_epoch,
          }
        : {
            body: this.state.body,
            language: this.state.language,
            expected_version: b.detail.version,
            membership_id: b.membership.id,
          },
    };
  }
  async send() {
    if (this.state.draft) return;
    const command = this.replyCommand();
    if (!command) return;
    const channel = this.state.channel;
    const pending: Pending = {
      kind: 'send',
      key: crypto.randomUUID(),
      conversationId: command.cid,
      channel,
      draftId: null,
    };
    if (
      await this.perform(
        pending,
        `/staff/cases/${this.caseId}/${command.cid ? 'messages' : 'email-messages'}`,
        command.body,
        (v) => decodeStaffReceipt(v, this.caseId, command.cid, channel),
        () =>
          this.set({ body: '', reviewed: '', draft: null, notice: 'saved' }),
      )
    )
      await this.refreshCase();
  }
  async translate(targetLanguage: 'en' | 'id') {
    if (this.state.draft || targetLanguage === this.state.language) return;
    const command = this.replyCommand();
    if (!command) return;
    const sourceLanguage = this.state.language;
    const pending: Pending = {
      kind: 'translate',
      key: crypto.randomUUID(),
      conversationId: command.cid,
      channel: this.state.channel,
      draftId: null,
    };
    await this.perform(
      pending,
      `/staff/cases/${this.caseId}/${command.cid ? '' : 'email-'}translation-drafts`,
      {
        ...command.body,
        target_language: targetLanguage,
        request_key: pending.key,
      },
      (v) => {
        const draft = decodeStaffTranslation(v, this.caseId, command.cid);
        if (
          draft.original_body !== command.body.body ||
          draft.source_language !== sourceLanguage ||
          draft.target_language !== targetLanguage ||
          draft.channel !== pending.channel
        )
          throw new ApiFailure('invalid-response', 'unknown');
        return draft;
      },
      (draft) =>
        this.set({
          draft,
          reviewed: draft.translated_body ?? '',
          notice: 'translation-ready',
        }),
    );
  }
  async approve() {
    const draft = this.state.draft;
    if (
      !draft ||
      draft.state !== 'ready' ||
      !this.canReply(draft.channel) ||
      !this.state.reviewed.trim() ||
      [...this.state.reviewed].length > 4000 ||
      this.state.reviewed.includes('\0')
    )
      return;
    const pending: Pending = {
      kind: 'approve',
      key: crypto.randomUUID(),
      conversationId: draft.conversation_id,
      channel: draft.channel,
      draftId: draft.id,
    };
    if (
      await this.perform(
        pending,
        `/staff/cases/${this.caseId}/${draft.conversation_id ? '' : 'email-'}translation-drafts/${draft.id}/send`,
        {
          text: this.state.reviewed,
          approved: true,
          expected_revision: draft.revision,
        },
        (v) =>
          decodeStaffReceipt(
            v,
            this.caseId,
            draft.conversation_id,
            draft.channel,
          ),
        () =>
          this.set({ body: '', reviewed: '', draft: null, notice: 'saved' }),
      )
    )
      await this.refreshCase();
  }
  async recover() {
    this.sync();
    const b = this.basis(),
      pending = this.state.pending;
    if (
      !this.connected ||
      !b ||
      !pending ||
      this.busy() ||
      Date.now() < this.queue.staff.readDeadline('action')
    )
      return;
    this.cancel();
    const serial = this.serial;
    this.controller = new AbortController();
    this.set({ phase: 'checking', error: null });
    let completed = false;
    try {
      if (pending.kind === 'send') {
        await this.read(
          b,
          serial,
          `/staff/cases/${this.caseId}/message-receipts/${pending.key}?membership_id=${b.membership.id}`,
          (v) =>
            decodeStaffReceipt(
              v,
              this.caseId,
              pending.conversationId,
              pending.channel,
            ),
        );
        if (!this.current(b, serial)) return;
        this.set({
          body: '',
          reviewed: '',
          draft: null,
          notice: 'saved',
          channel: pending.channel,
        });
        completed = true;
      } else if (pending.kind === 'accept' && pending.conversationId) {
        const chat = await this.read(
          b,
          serial,
          `/staff/conversations/${pending.conversationId}`,
          (v) => decodeStaffChat(v, this.caseId, pending.conversationId!),
        );
        if (!this.current(b, serial)) return;
        this.set({
          chat,
          notice:
            chat.mode === 'human' &&
            chat.handler_membership_id === b.membership.id
              ? 'accepted'
              : 'changed',
        });
        completed = chat.mode !== 'waiting_staff';
      } else {
        const prefix = `/staff/cases/${this.caseId}/${pending.conversationId ? '' : 'email-'}translation-`;
        const path =
          pending.kind === 'translate'
            ? prefix + `requests/${pending.key}`
            : prefix + `drafts/${pending.draftId}`;
        const draft = await this.read(b, serial, path, (v) =>
          decodeStaffTranslation(v, this.caseId, pending.conversationId),
        );
        if (!this.current(b, serial)) return;
        if (pending.kind === 'approve') {
          completed = draft.state === 'sent';
          if (completed)
            this.set({
              body: '',
              draft: null,
              reviewed: '',
              notice: 'saved',
              channel: draft.channel,
              language: draft.source_language,
            });
        } else {
          this.set({
            body: draft.original_body,
            draft,
            reviewed: draft.translated_body ?? '',
            channel: draft.channel,
            language: draft.source_language,
            notice:
              draft.state === 'ready'
                ? 'translation-ready'
                : draft.state === 'failed'
                  ? null
                  : 'unknown',
          });
          completed = draft.state !== 'generating';
        }
      }
      if (!this.current(b, serial)) return;
      if (completed) this.complete(b);
      else this.set({ phase: 'unknown', notice: 'unknown' });
    } catch (e) {
      if (this.current(b, serial)) {
        this.readFailure(e);
        this.set({ phase: 'unknown', notice: 'unknown' });
      }
    }
    if (completed && pending.kind !== 'translate') await this.refreshCase();
  }
}
