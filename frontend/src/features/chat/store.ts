import { ConversationTimeline } from './timeline';
import { AgentStore, type AgentBasis } from './agent-store';
import { QueryClient } from '@tanstack/react-query';
import type { AuthCoordinator } from '../../app/auth/coordinator';
import type { BoundarySnapshot } from '../../api/contracts';
import { ApiFailure } from '../../api/errors';
import { decodeConversation, decodeReceipt, messageSchema } from './contracts';
import type { Conversation, MessageInput } from './contracts';
export interface LocalMessage {
  clientId: string;
  body: string;
  language: 'en' | 'id';
  state: 'sending' | 'received' | 'unknown' | 'rejected';
  receipt?: {
    message_id: string;
    sequence: number;
    processing_state: 'received';
  };
}
interface Retained {
  input: MessageInput;
  messages: readonly LocalMessage[];
  unresolved: boolean;
  reviewNeeded: boolean;
}
export interface ChatState extends Retained {
  phase:
    'idle' | 'loading' | 'ready' | 'busy' | 'unavailable' | 'unknown-create';
  meta: Conversation | null;
  error: ApiFailure | null;
}
const blank = (): Retained => ({
  input: { body: '', service: '' },
  messages: [],
  unresolved: false,
  reviewNeeded: false,
});
const fail = (error: unknown) =>
  error instanceof ApiFailure
    ? error
    : new ApiFailure('invalid-response', 'unknown');
export class ChatStore {
  readonly agent: AgentStore;
  readonly timeline: ConversationTimeline;
  private historyActive = false;
  private historyVisible = false;
  private state: ChatState = {
    phase: 'idle',
    meta: null,
    error: null,
    ...blank(),
  };
  private listeners = new Set<() => void>();
  private serial = 0;
  private owner: BoundarySnapshot | null = null;
  private requestedId: string | null = null;
  get requestedConversationId() {
    return this.owner && this.auth.isCurrent(this.owner)
      ? this.requestedId
      : null;
  }
  private working = false;
  private createUncertain = false;
  private openFlight: {
    id: string;
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
    private scope: string,
  ) {
    this.agent = new AgentStore(auth, {
      basis: () => this.agentBasis(),
      compatible: (basis) => this.agentCompatible(basis),
      hold: () => this.holdAgent(),
      refresh: () => {
        this.timeline.reload();
      },
      revoke: (error) => this.revokeConversation(error),
    });
    this.timeline = new ConversationTimeline(
      auth,
      async (page, signal, current) => {
        const meta = this.state.meta;
        if (!meta || this.timeline.getState().id !== meta.conversation_id)
          return;
        if (
          meta.version === page.version &&
          meta.generation_epoch === page.generation_epoch
        )
          return;
        const fresh = await auth.readResource({
          method: 'GET',
          path: this.path(meta.conversation_id),
          boundary: auth.capture(),
          signal,
          isRelevant: current,
          successStatus: 200,
          decode: decodeConversation,
        });
        if (!current()) return;
        if (
          fresh.conversation_id !== meta.conversation_id ||
          fresh.version !== page.version ||
          fresh.generation_epoch !== page.generation_epoch
        )
          throw new ApiFailure('stale', 'rejected');
        this.set({ meta: fresh });
      },
      (error) => {
        this.revokeConversation(error);
      },
    );
    this.timeline.subscribe(() => this.agent.reconcile());
  }

  private revokeConversation(error: ApiFailure) {
    this.openFlight = null;
    const id = this.state.meta?.conversation_id ?? this.timeline.getState().id;
    if (id) {
      this.agent.forget(id);
      if (this.owner) this.auth.forgetRetained(this.path(id), this.owner);
    }
    this.timeline.reset();
    this.serial++;
    this.working = false;
    this.queries.clear();
    this.set({ phase: 'unavailable', meta: null, error, ...blank() });
  }

  private agentBasis(): AgentBasis | null {
    const meta = this.state.meta,
      history = this.timeline.getState();
    if (
      this.working ||
      this.state.phase !== 'ready' ||
      !meta ||
      this.state.input.body.trim() ||
      this.state.unresolved ||
      this.state.messages.some((m) => m.state !== 'received') ||
      history.id !== meta.conversation_id ||
      history.phase !== 'ready' ||
      history.hasMore ||
      history.snapshot?.mode !== 'ai' ||
      history.snapshot.version !== meta.version ||
      history.snapshot.generation_epoch !== meta.generation_epoch
    )
      return null;
    const latest = history.items
      .filter((event) => 'message_id' in event.payload)
      .at(-1);
    if (!latest || latest.event_type !== 'message_received') return null;
    const local = this.state.messages.at(-1);
    if (
      local?.receipt &&
      local.receipt.message_id !== latest.payload.message_id
    )
      return null;
    return {
      conversationId: meta.conversation_id,
      messageId: latest.payload.message_id,
      version: meta.version,
      epoch: meta.generation_epoch,
      language: meta.language,
    };
  }
  private agentCompatible(basis: AgentBasis) {
    const meta = this.state.meta,
      history = this.timeline.getState();
    return (
      !!meta &&
      meta.conversation_id === basis.conversationId &&
      meta.language === basis.language &&
      meta.generation_epoch === basis.epoch &&
      history.id === basis.conversationId &&
      (history.snapshot
        ? history.snapshot.mode === 'ai' &&
          history.snapshot.generation_epoch === basis.epoch
        : history.phase === 'loading' || history.phase === 'paused')
    );
  }
  private holdAgent() {
    if (this.working || this.state.phase !== 'ready')
      throw new ApiFailure('stale', 'not-sent');
    const serial = ++this.serial;
    this.working = true;
    this.set({ phase: 'busy' });
    // Event polling deliberately continues: remote handoff/epoch changes must
    // invalidate the work while the local composer is locked.
    return () => {
      if (this.serial !== serial) return;
      this.working = false;
      if (this.state.phase === 'busy') this.set({ phase: 'ready' });
    };
  }

  observeHistory(id: string, enabled = true) {
    this.historyActive = true;
    this.historyVisible = enabled;
    const meta = this.state.meta;
    if (meta?.conversation_id === id) {
      try {
        this.timeline.start(id, meta.version, meta.generation_epoch, enabled);
      } catch {
        this.timeline.reset();
      }
    }
    return () => {
      this.historyActive = false;
      this.historyVisible = false;
      this.timeline.reset();
    };
  }
  pauseHistory() {
    this.historyVisible = false;
    if (this.state.meta) this.agent.forget(this.state.meta.conversation_id);
    this.timeline.pause();
  }
  resumeHistory() {
    this.historyVisible = true;
    this.resumeAfterWrite();
  }
  private resumeAfterWrite() {
    if (this.historyActive && this.historyVisible && this.state.meta)
      this.timeline.resume(
        this.state.meta.version,
        this.state.meta.generation_epoch,
      );
  }
  get historyIsVisible() {
    return this.historyVisible;
  }
  get historyRequired() {
    return this.historyActive;
  }
  receivedMessages() {
    const timeline = this.timeline.getState();
    const events = timeline.phase === 'ready' ? timeline.items : [];
    const saved = events
      .filter((e) => e.event_type === 'message_received')
      .map((e) => ({
        messageId: e.payload.message_id,
        body: e.payload.body,
        language: e.payload.language,
        eventSequence: e.sequence,
      }));
    const ids = new Set(saved.map((m) => m.messageId));
    return [
      ...saved,
      ...this.state.messages
        .filter(
          (m) =>
            m.state === 'received' &&
            (!m.receipt || !ids.has(m.receipt.message_id)),
        )
        .map((m) => ({
          messageId: m.receipt?.message_id ?? null,
          body: m.body,
          language: m.language,
          eventSequence: null,
        })),
    ];
  }

  getState = () => this.state;
  subscribe = (fn: () => void) => {
    this.listeners.add(fn);
    return () => {
      this.listeners.delete(fn);
    };
  };
  private set(value: Partial<ChatState>) {
    this.state = { ...this.state, ...value };
    this.listeners.forEach((fn) => fn());
  }
  connect() {
    const offAgent = this.agent.connect();
    const offSource = this.auth.onSourceUnavailable((error) =>
      this.revokeConversation(error),
    );
    const off = this.auth.onReset(() => {
      this.timeline.reset();
      this.serial++;
      this.working = false;
      this.owner = null;
      this.requestedId = null;
      this.openFlight = null;
      this.queries.clear();
      this.set({
        phase: this.createUncertain ? 'unknown-create' : 'idle',
        meta: null,
        error: null,
        ...blank(),
      });
    });
    return () => {
      offAgent();
      offSource();
      off();
    };
  }
  private path(id: string) {
    return '/conversations/' + id;
  }
  private save() {
    if (this.owner && this.state.meta && this.auth.isCurrent(this.owner))
      this.auth.keep(
        this.path(this.state.meta.conversation_id),
        {
          input: this.state.input,
          messages: this.state.messages.filter((m) => m.state === 'received'),
          unresolved:
            this.state.unresolved ||
            this.state.messages.some(
              (m) => m.state === 'sending' || m.state === 'unknown',
            ),
          reviewNeeded:
            this.state.reviewNeeded ||
            this.state.messages.some((m) => m.state === 'rejected'),
        },
        this.owner,
      );
  }
  input(value: MessageInput) {
    if (this.state.phase !== 'ready' || this.working) return;
    this.auth.capture();
    this.set({ input: value });
    this.save();
  }
  discard() {
    if (this.state.meta) this.agent.forget(this.state.meta.conversation_id);
    if (this.owner && this.state.meta)
      this.auth.forgetRetained(
        this.path(this.state.meta.conversation_id),
        this.owner,
      );
    this.timeline.reset();
    this.requestedId = null;
    this.serial++;
    this.working = false;
    this.queries.clear();
    this.set({
      phase: this.createUncertain ? 'unknown-create' : 'idle',
      meta: null,
      error: null,
      ...blank(),
    });
  }
  newEntry() {
    this.requestedId = null;
    if (!this.working && this.state.phase !== 'unknown-create') this.discard();
  }
  private async metadata(id: string, owner: BoundarySnapshot, serial: number) {
    const meta = await this.queries.fetchQuery({
      queryKey: [
        this.scope,
        owner.boundaryId,
        owner.authEpoch,
        owner.sessionKind,
        owner.principalId,
        'conversation',
        id,
      ],
      queryFn: ({ signal }) =>
        this.auth.readResource({
          path: this.path(id),
          method: 'GET',
          boundary: owner,
          isRelevant: () => this.serial === serial,
          signal,
          successStatus: 200,
          decode: decodeConversation,
        }),
    });
    if (meta.conversation_id !== id)
      throw new ApiFailure('invalid-response', 'rejected');
    if (this.serial !== serial || !this.auth.isCurrent(owner))
      throw new ApiFailure('stale', 'not-sent');
    return meta;
  }
  open(id: string): Promise<void> {
    const owner = this.auth.capture();
    if (owner.sessionKind === 'staff') return Promise.resolve();
    if (this.openFlight?.id === id && this.openFlight.epoch === owner.authEpoch)
      return this.openFlight.promise;
    if (this.working) return Promise.resolve();
    this.requestedId = id;
    const serial = ++this.serial;
    this.owner = owner;
    this.set({ phase: 'loading', meta: null, error: null, ...blank() });
    const promise = (async () => {
      try {
        const meta = await this.metadata(id, owner, serial);
        const retained = this.auth.retained<Retained>(this.path(id), owner);
        this.set({
          phase: 'ready',
          meta,
          error: null,
          ...(retained ?? blank()),
        });
        this.save();
      } catch (e) {
        if (this.serial === serial)
          this.set({
            phase: 'unavailable',
            meta: null,
            error: fail(e),
            ...blank(),
          });
      } finally {
        if (
          this.openFlight?.id === id &&
          this.openFlight.epoch === owner.authEpoch &&
          this.serial === serial
        )
          this.openFlight = null;
      }
    })();
    this.openFlight = { id, epoch: owner.authEpoch, promise };
    return promise;
  }
  async create(language: 'en' | 'id'): Promise<string | null> {
    if (
      this.working ||
      this.createUncertain ||
      this.state.phase === 'unknown-create'
    )
      return null;
    this.working = true;
    let serial = ++this.serial;
    this.set({ phase: 'busy', error: null });
    try {
      if (this.auth.getState().phase === 'guest') {
        const result = await this.auth.change({ kind: 'bootstrap' });
        if (
          result.outcome !== 'confirmed' ||
          result.state.phase !== 'ready' ||
          result.state.kind !== 'anonymous'
        )
          throw new ApiFailure('stale', 'not-sent');
        serial = ++this.serial;
        this.working = true;
        this.set({ phase: 'busy' });
      }
      const owner = this.auth.capture();
      if (owner.sessionKind === 'staff')
        throw new ApiFailure('invalid-request', 'not-sent');
      this.owner = owner;
      this.createUncertain = true;
      const meta = await this.auth.request({
        path: '/conversations',
        method: 'POST',
        body: { language },
        boundary: owner,
        isRelevant: () => this.serial === serial,
        successStatus: 201,
        decode: decodeConversation,
      });
      if (this.serial !== serial) return null;
      this.createUncertain = false;
      this.requestedId = meta.conversation_id;
      this.set({ phase: 'ready', meta, error: null, ...blank() });
      this.save();
      return meta.conversation_id;
    } catch (e) {
      if (this.serial === serial) {
        const error = fail(e);
        this.createUncertain = error.outcome === 'unknown';
        this.set({
          phase: this.createUncertain ? 'unknown-create' : 'unavailable',
          meta: null,
          error,
        });
      }
      return null;
    } finally {
      if (this.serial === serial) this.working = false;
    }
  }
  async send(retry = false): Promise<void> {
    if (
      this.working ||
      this.state.phase !== 'ready' ||
      !this.state.meta ||
      this.state.unresolved ||
      (this.historyActive && !this.timeline.canSend())
    )
      return;
    const pending = this.state.messages.find((m) => m.state !== 'received');
    if (pending && !retry) return;
    if (
      retry &&
      (!pending ||
        pending.state === 'sending' ||
        this.state.error?.code === 'MESSAGE_CONFLICT')
    )
      return;
    const owner = this.auth.capture(),
      meta = this.state.meta,
      serial = ++this.serial;
    if (owner.sessionKind === 'staff') return;
    let message: LocalMessage;
    if (pending) message = { ...pending, state: 'sending' };
    else {
      const input = messageSchema.parse(this.state.input);
      const prefix = input.service ? `${input.service}\n\n` : '';
      message = {
        clientId: crypto.randomUUID(),
        body: prefix + input.body,
        language: meta.language,
        state: 'sending',
      };
    }
    this.timeline.pause();
    this.working = true;
    this.owner = owner;
    this.set({
      phase: 'busy',
      error: null,
      messages: pending
        ? this.state.messages.map((m) =>
            m.clientId === message.clientId ? message : m,
          )
        : [...this.state.messages, message],
    });
    this.save();
    try {
      const receipt = await this.auth.request({
        path: this.path(meta.conversation_id) + '/messages',
        method: 'POST',
        body: { client_message_id: message.clientId, body: message.body },
        boundary: owner,
        isRelevant: () => this.serial === serial,
        successStatus: 201,
        decode: decodeReceipt,
      });
      if (this.serial !== serial) return;
      this.set({
        messages: this.state.messages.map((m) =>
          m.clientId === message.clientId
            ? { ...m, state: 'received', receipt }
            : m,
        ),
        input: { body: '', service: '' },
        reviewNeeded: false,
      });
      this.save();
      const fresh = await this.metadata(meta.conversation_id, owner, serial);
      this.set({ phase: 'ready', meta: fresh, error: null });
      this.save();
    } catch (e) {
      if (this.serial === serial) {
        const error = fail(e);
        const alreadyReceived = this.state.messages.some(
          (m) => m.clientId === message.clientId && m.state === 'received',
        );
        if (error.status === 403 && !alreadyReceived) {
          this.set({
            messages: this.state.messages.map((m) =>
              m.clientId === message.clientId ? { ...m, state: 'rejected' } : m,
            ),
            error,
            reviewNeeded: true,
          });
          this.save();
          await this.auth.recheck();
          return;
        }
        if (error.status === 403 || error.status === 404) {
          this.auth.forgetRetained(this.path(meta.conversation_id), owner);
          this.set({ phase: 'unavailable', meta: null, error, ...blank() });
          return;
        }
        const received = this.state.messages.some(
          (m) => m.clientId === message.clientId && m.state === 'received',
        );
        this.set({
          phase: received ? 'unavailable' : 'ready',
          error,
          messages: this.state.messages.map((m) =>
            m.clientId === message.clientId && !received
              ? {
                  ...m,
                  state: error.outcome === 'unknown' ? 'unknown' : 'rejected',
                }
              : m,
          ),
        });
        this.save();
      }
    } finally {
      if (this.serial === serial) {
        this.working = false;
        this.resumeAfterWrite();
      }
    }
  }
  async language(language: 'en' | 'id'): Promise<void> {
    if (
      this.working ||
      this.state.phase !== 'ready' ||
      !this.state.meta ||
      this.state.unresolved ||
      this.state.messages.some((m) => m.state !== 'received') ||
      (this.historyActive && !this.timeline.canSend())
    )
      return;
    const owner = this.auth.capture(),
      meta = this.state.meta;
    if (meta.language === language) return;
    const serial = ++this.serial;
    this.timeline.pause();
    this.working = true;
    this.set({ phase: 'busy', error: null });
    try {
      const fresh = await this.auth.mutateResource(
        this.path(meta.conversation_id),
        {
          path: this.path(meta.conversation_id) + '/language',
          method: 'PATCH',
          boundary: owner,
          isRelevant: () => this.serial === serial,
          body: { language, expected_version: meta.version },
          successStatus: 200,
          decode: decodeConversation,
        },
      );
      if (fresh.conversation_id !== meta.conversation_id)
        throw new ApiFailure('invalid-response', 'unknown');
      if (this.serial === serial) {
        this.set({ phase: 'ready', meta: fresh });
        this.save();
      }
    } catch (e) {
      if (this.serial === serial) {
        const error = fail(e);
        if (error.status === 403 || error.status === 404) {
          this.auth.forgetRetained(this.path(meta.conversation_id), owner);
          this.set({ phase: 'unavailable', meta: null, error, ...blank() });
        } else {
          this.set({ phase: 'unavailable', error });
          this.save();
        }
      }
    } finally {
      if (this.serial === serial) {
        this.working = false;
        this.resumeAfterWrite();
      }
    }
  }
  plainReport(): { body: string; service: MessageInput['service'] } {
    const owner = this.auth.capture();
    if (
      !this.owner ||
      !this.auth.isCurrent(this.owner) ||
      owner.sessionKind === 'staff' ||
      this.state.phase !== 'ready' ||
      (this.historyActive && !this.timeline.canSend())
    )
      throw new ApiFailure('stale', 'not-sent');
    return {
      body: [
        ...this.receivedMessages().map((m) => m.body),
        this.state.input.body,
      ]
        .filter(Boolean)
        .join('\n\n'),
      service: this.state.input.service,
    };
  }
  prefillReviewedReport(value: MessageInput) {
    const owner = this.auth.capture();
    if (
      owner.sessionKind !== 'customer' ||
      this.state.phase !== 'ready' ||
      this.state.messages.length
    )
      throw new ApiFailure('stale', 'not-sent');
    this.input(messageSchema.parse(value));
  }
  dispose() {
    this.agent.reset();
    this.timeline.dispose();
    this.historyActive = false;
    this.serial++;
    this.openFlight = null;
    this.requestedId = null;
    this.working = false;
    this.queries.clear();
    this.listeners.clear();
  }
}
