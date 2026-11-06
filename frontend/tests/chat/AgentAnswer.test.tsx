import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, expect, it } from 'vitest';
import { AgentAnswer } from '../../src/features/chat/AgentAnswer';
import { fixture, ID, OTHER, json, error } from './fixture';

const RUN = '33333333-3333-4333-8333-333333333333';
const owned: (() => void)[] = [];
afterEach(() => owned.splice(0).forEach((dispose) => dispose()));
async function setup(locale: 'en' | 'id' = 'en') {
  const f = fixture();
  await f.auth.start();
  f.server.meta.language = locale;
  f.server.meta.last_sequence = 1;
  f.server.events = [
    {
      id: OTHER,
      sequence: 1,
      created_at: '2026-11-05T12:00:00Z',
      event_type: 'message_received',
      payload: {
        message_id: OTHER,
        body: 'My saved question.',
        author_type: 'customer',
        language: locale,
      },
    },
  ];
  await f.store.open(ID);
  const off = f.store.observeHistory(ID);
  const calls = {
    starts: 0,
    answers: 0,
    events: 0,
    answer: async () => error(503, 'AGENT_UNAVAILABLE'),
  };
  f.server.override = async (path) => {
    if (path.endsWith('/agent-runs')) {
      calls.starts++;
      return json({ run_id: RUN, state: 'prepared' }, 201);
    }
    if (path.endsWith('/resume')) {
      calls.answers++;
      return calls.answer();
    }
    if (path.endsWith('/me'))
      return json({ session_kind: 'anonymous', principal_id: null });
    if (path === '/api/v1/conversations/' + ID) return json(f.server.meta);
    if (path.includes('/events?')) {
      calls.events++;
      const position = Number(
        new URL(path, 'https://fixture.invalid').searchParams
          .get('after')
          ?.slice(1) ?? 0,
      );
      return json({
        items: f.server.events.slice(position),
        next_cursor: 'c' + f.server.events.length,
        has_more: false,
        mode: f.server.mode,
        generation_epoch: f.server.meta.generation_epoch,
        version: f.server.meta.version,
      });
    }
    return error(404, 'RESOURCE_NOT_FOUND');
  };
  const view = render(
    <MemoryRouter>
      <AgentAnswer
        store={f.store}
        conversationId={ID}
        routeKey="a"
        locale={locale}
      />
    </MemoryRouter>,
  );
  owned.push(() => {
    view.unmount();
    off();
    f.dispose();
  });
  await waitFor(() =>
    expect(
      screen.getByRole('button', {
        name: locale === 'en' ? 'Ask AI' : 'Tanya AI',
      }),
    ).toBeEnabled(),
  );
  return { ...f, ...view, calls };
}
it('only starts on explicit Ask AI and offers truthful fallback without technical identifiers', async () => {
  const f = await setup();
  expect(f.calls.starts).toBe(0);
  fireEvent.click(screen.getByRole('button', { name: 'Ask AI' }));
  expect(await screen.findByText(/The answer is not confirmed/)).toBeVisible();
  expect(f.calls.starts).toBe(1);
  expect(f.calls.answers).toBe(1);
  expect(screen.getByRole('link', { name: 'Get help' })).toHaveAttribute(
    'href',
    '/help/conversations/' + ID + '?topic=contact&lang=en',
  );
  expect(screen.queryByText(RUN)).not.toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: /Resume|Execute|Generate/ }),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Check saved updates' }));
  await waitFor(() => expect(f.calls.events).toBeGreaterThan(0));
  expect(f.calls.answers).toBe(1);
});
it('keeps event polling active and rejects a late answer after remote handoff; local send is locked', async () => {
  const f = await setup();
  let resolve!: (value: Response) => void;
  f.calls.answer = () =>
    new Promise<Response>((r) => {
      resolve = r;
    });
  fireEvent.click(screen.getByRole('button', { name: 'Ask AI' }));
  await waitFor(() => expect(f.calls.answers).toBe(1));
  expect(f.store.getState().phase).toBe('busy');
  await act(async () => {
    await f.store.send();
  });
  expect(f.server.posts).toBe(0);
  f.server.mode = 'waiting_staff';
  f.server.meta.version++;
  f.server.meta.generation_epoch++;
  await act(async () => {
    await f.store.timeline.refresh();
  });
  expect(f.calls.events).toBeGreaterThan(0);
  expect(f.store.getState().phase).toBe('ready');
  await act(async () => {
    resolve(
      json({ run_id: RUN, state: 'published', message_id: ID, sequence: 2 }),
    );
  });
  expect(f.store.agent.getState().outcome).toBeNull();
  expect(f.calls.answers).toBe(1);
});
it('uses published receipts only as pointers while the event feed remains the answer source', async () => {
  const f = await setup();
  f.calls.answer = async () =>
    json({ run_id: RUN, state: 'published', message_id: ID, sequence: 2 });
  fireEvent.click(screen.getByRole('button', { name: 'Ask AI' }));
  expect(await screen.findByText(/An answer was saved/)).toBeVisible();
  expect(screen.queryByText('My saved question.')).not.toBeInTheDocument();
  expect(
    f.store.timeline
      .getState()
      .items.filter((item) => item.event_type === 'ai_message'),
  ).toHaveLength(0);
  expect(f.calls.answers).toBe(1);
});
it('accepts its own publication version advance without replaying the model', async () => {
  const f = await setup();
  let resolve!: (value: Response) => void;
  f.calls.answer = () =>
    new Promise<Response>((r) => {
      resolve = r;
    });
  fireEvent.click(screen.getByRole('button', { name: 'Ask AI' }));
  await waitFor(() => expect(f.calls.answers).toBe(1));
  f.server.meta.version++;
  f.server.meta.last_sequence++;
  f.server.events.push({
    id: ID,
    sequence: 2,
    event_type: 'ai_message',
    created_at: '2026-11-05T12:01:00Z',
    payload: {
      message_id: ID,
      body: 'Authoritative saved answer.',
      language: 'en',
      author_type: 'ai',
    },
  });
  await act(async () => {
    await f.store.timeline.refresh();
  });
  expect(f.store.getState().phase).toBe('busy');
  await act(async () => {
    resolve(
      json({ run_id: RUN, state: 'published', message_id: ID, sequence: 2 }),
    );
  });
  expect(f.store.agent.getState().phase).toBe('published');
  expect(
    f.store.timeline
      .getState()
      .items.some((item) => item.event_type === 'ai_message'),
  ).toBe(true);
  expect(f.calls.starts).toBe(1);
  expect(f.calls.answers).toBe(1);
});
it('Indonesian review/draft copy points to existing confirmation, without auto-confirm', async () => {
  const f = await setup('id');
  f.calls.answer = async () =>
    json({ run_id: RUN, state: 'draft', draft_id: OTHER });
  fireEvent.click(screen.getByRole('button', { name: 'Tanya AI' }));
  expect(
    await screen.findByText(/Belum ada pengajuan sampai Anda mengonfirmasi/),
  ).toBeVisible();
  expect(
    screen.getByRole('link', { name: 'Tinjau permintaan bantuan' }),
  ).toHaveAttribute('href', '/help/submissions/' + OTHER + '?lang=id');
  expect(f.calls.answers).toBe(1);
  expect(f.server.posts).toBe(0);
});
