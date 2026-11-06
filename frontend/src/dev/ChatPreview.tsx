/** Controlled visual fixture only. Production routing excludes this module. */
import { useEffect, useMemo } from 'react';
import { useParams, useLocation } from 'react-router';
import { AuthCoordinator } from '../app/auth/coordinator';
import { ChatStore } from '../features/chat/store';
import { ChatContext } from '../features/chat/context';
import { ChatPage } from '../features/chat/ChatPage';
import type { ConversationEvent } from '../features/chat/event-contracts';
export function Component() {
  const { conversationId } = useParams(),
    location = useLocation();
  const ended = new URLSearchParams(location.search).get('mode') === 'ended';
  const fixture = useMemo(() => {
    const id = conversationId!,
      caseId = '22222222-2222-4222-8222-222222222222';
    const meta = {
      conversation_id: id,
      version: 6,
      generation_epoch: 3,
      last_sequence: 3,
      language: 'en',
    };
    const event = (type: string, payload: unknown, n: number) =>
      ({
        id: '70000000-0000-4000-8000-' + String(n).padStart(12, '0'),
        sequence: n,
        event_type: type,
        created_at: '2026-11-04T12:00:00Z',
        payload,
      }) as ConversationEvent;
    const message = (
      body: string,
      author: 'customer' | 'staff' | 'ai',
      n: number,
    ) =>
      event(
        author === 'customer'
          ? 'message_received'
          : author === 'staff'
            ? 'staff_message'
            : 'ai_message',
        {
          message_id: '80000000-0000-4000-8000-' + String(n).padStart(12, '0'),
          body,
          language: 'en',
          author_type: author,
        },
        n,
      );
    const events = [
      message(
        'My flight has been delayed. My parking booking ends tonight. What should I do?',
        'customer',
        1,
      ),
      message(
        'I can help explain the next step. To request a change, support will need your booking reference and updated departure time.',
        'ai',
        2,
      ),
      event('handoff_requested', { case_id: caseId, mode: 'waiting_staff' }, 3),
      event('chat_accepted', { case_id: caseId, mode: 'human' }, 4),
      message(
        'Hello, I have your original message. Please confirm the new departure time so I can ask the parking team to check your options.',
        'staff',
        5,
      ),
    ];
    if (ended)
      events.push(event('chat_ended', { case_id: caseId, mode: 'ended' }, 6));
    const json = (v: unknown, status = 200) =>
      new Response(JSON.stringify(v), {
        status,
        headers: { 'Content-Type': 'application/json' },
      });
    const receipts = new Map<string, unknown>();
    const auth = new AuthCoordinator({
      readProof: () => 'p'.repeat(43),
      fetch: async (url, init) => {
        const path = String(url),
          u = new URL(path, 'https://fixture.invalid');
        if (u.pathname === '/api/v1/me')
          return json({ session_kind: 'anonymous', principal_id: null });
        if (u.pathname === '/api/v1/conversations/' + id) return json(meta);
        if (u.pathname.endsWith('/events')) {
          const after = Number(u.searchParams.get('after')?.slice(1) ?? 0),
            items = events.slice(after, after + 50);
          return json({
            items,
            next_cursor: 'c' + (after + items.length),
            has_more: after + items.length < events.length,
            mode: ended ? 'ended' : 'human',
            version: meta.version,
            generation_epoch: meta.generation_epoch,
          });
        }
        if (u.pathname.endsWith('/messages') && !ended) {
          const body = JSON.parse(String(init?.body));
          if (!receipts.has(body.client_message_id)) {
            const m = message(body.body, 'customer', events.length + 1);
            events.push(m);
            meta.version++;
            meta.last_sequence++;
            receipts.set(body.client_message_id, {
              message_id:
                'message_id' in m.payload ? m.payload.message_id : null,
              sequence: meta.last_sequence,
              processing_state: 'received',
            });
          }
          return json(receipts.get(body.client_message_id), 201);
        }
        return json({}, 404);
      },
    });
    const store = new ChatStore(auth, 'preview'),
      off = store.connect();
    let generation = 0;
    return {
      auth,
      store,
      off,
      acquire: () => ++generation,
      isCurrent: (value: number) => value === generation,
    };
  }, [conversationId, ended]);
  useEffect(() => {
    const generation = fixture.acquire();
    void fixture.auth.start();
    return () => {
      queueMicrotask(() => {
        if (fixture.isCurrent(generation)) {
          fixture.off();
          fixture.store.dispose();
          fixture.auth.dispose();
        }
      });
    };
  }, [fixture]);
  return (
    <ChatContext value={fixture.store}>
      <p role="note" style={{ padding: '10px 24px', background: '#fff5df' }}>
        Design preview — Synthetic persisted conversation. No service or model
        requests.
      </p>
      <ChatPage />
    </ChatContext>
  );
}
