import { vi } from 'vitest';
import { AuthCoordinator } from '../../src/app/auth/coordinator';
import type { ConversationEvent } from '../../src/features/chat/event-contracts';
import { ChatStore } from '../../src/features/chat/store';
export const ID = '11111111-1111-4111-8111-111111111111';
export const OTHER = '22222222-2222-4222-8222-222222222222';
export const json = (v: unknown, status = 200) =>
  new Response(JSON.stringify(v), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
export const error = (status: number, code: string) =>
  json(
    {
      error: {
        code,
        message: 'DO NOT DISPLAY',
        request_id: ID,
        retryable: false,
      },
    },
    status,
  );
export function fixture() {
  const server = {
    proof: 'p'.repeat(43),
    kind: 'anonymous',
    principal: null as string | null,
    creates: 0,
    posts: 0,
    patches: 0,
    meta: {
      conversation_id: ID,
      language: 'en',
      version: 1,
      generation_epoch: 0,
      last_sequence: 0,
    },
    events: [] as ConversationEvent[],
    mode: 'ai' as 'ai' | 'waiting_staff' | 'human' | 'ended',
    messages: new Map<
      string,
      {
        body: string;
        message_id: string;
        sequence: number;
        processing_state: 'received';
      }
    >(),
    loseCreate: false,
    loseAppend: false,
    conflictLanguage: false,
    override: null as
      null | ((url: string, init?: RequestInit) => Promise<Response>),
  };
  const fetcher = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input),
        method = init?.method ?? 'GET';
      if (server.override) return server.override(path, init);
      if (path === '/api/v1/me')
        return json({
          session_kind: server.kind,
          principal_id: server.principal,
        });
      if (path === '/api/v1/conversations' && method === 'POST') {
        server.creates++;
        server.meta.language = JSON.parse(String(init?.body)).language;
        if (server.loseCreate) throw Error('response lost');
        return json(server.meta, 201);
      }
      if (path === '/api/v1/conversations/' + ID && method === 'GET')
        return json(server.meta);
      if (path.startsWith('/api/v1/conversations/' + ID + '/events?')) {
        const params = new URL(path, 'https://fixture.invalid').searchParams;
        const position = Number(params.get('after')?.slice(1) ?? 0),
          limit = Number(params.get('limit'));
        const items = server.events.slice(position, position + limit);
        return json({
          items,
          next_cursor: 'c' + (position + items.length),
          has_more: position + items.length < server.events.length,
          mode: server.mode,
          generation_epoch: server.meta.generation_epoch,
          version: server.meta.version,
        });
      }
      if (path.endsWith('/messages')) {
        server.posts++;
        const body = JSON.parse(String(init?.body));
        let message = server.messages.get(body.client_message_id);
        if (message && message.body !== body.body)
          return error(409, 'MESSAGE_CONFLICT');
        if (!message) {
          message = {
            body: body.body,
            message_id: crypto.randomUUID(),
            sequence: ++server.meta.last_sequence,
            processing_state: 'received',
          };
          server.meta.version++;
          server.messages.set(body.client_message_id, message);
          server.events.push({
            id: crypto.randomUUID(),
            sequence: server.events.length + 1,
            event_type: 'message_received',
            created_at: '2026-11-04T12:00:00Z',
            payload: {
              message_id: message.message_id,
              body: body.body,
              language: server.meta.language as 'en' | 'id',
              author_type: 'customer',
            },
          });
        }
        if (server.loseAppend) throw Error('response lost');
        return json(message, 201);
      }
      if (path.endsWith('/language')) {
        server.patches++;
        const body = JSON.parse(String(init?.body));
        if (
          server.conflictLanguage ||
          body.expected_version !== server.meta.version
        )
          return error(409, 'VERSION_CONFLICT');
        server.meta.language = body.language;
        server.meta.version++;
        server.meta.generation_epoch++;
        return json(server.meta);
      }
      return error(404, 'RESOURCE_NOT_FOUND');
    },
  );
  const auth = new AuthCoordinator({
    readProof: () => server.proof,
    fetch: fetcher,
  });
  const store = new ChatStore(auth, 'localhost-test'),
    off = store.connect();
  return {
    server,
    fetcher,
    auth,
    store,
    dispose() {
      off();
      store.dispose();
      auth.dispose();
    },
  };
}
