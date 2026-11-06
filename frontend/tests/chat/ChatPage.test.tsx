import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { StrictMode } from 'react';
import { createMemoryRouter } from 'react-router';
import { afterEach, expect, it, vi } from 'vitest';
import { App } from '../../src/app/App';
import { createBootstrap } from '../../src/app/config/publicSite';
import { createAppRoutes } from '../../src/app/routing/routes';
import { fixture, ID, OTHER, error } from './fixture';
const owned: ReturnType<typeof fixture>[] = [];
afterEach(() => owned.splice(0).forEach((f) => f.dispose()));
async function mount(
  path = '/help/new?topic=booking&lang=en',
  kind = 'anonymous',
  seed?: (server: ReturnType<typeof fixture>['server']) => void,
) {
  const f = fixture();
  owned.push(f);
  seed?.(f.server);
  f.server.kind = kind;
  if (kind === 'customer') f.server.principal = OTHER;
  await f.auth.start();
  const bootstrap = createBootstrap('localhost', '');
  if (bootstrap.status !== 'configured') throw Error('fixture');
  const router = createMemoryRouter(
    createAppRoutes(bootstrap.site, undefined, [], f.auth),
    { initialEntries: [path] },
  );
  render(
    <StrictMode>
      <App bootstrap={bootstrap} router={router} />
    </StrictMode>,
  );
  await screen.findByRole('region', { name: 'Conversation' });
  return { ...f, router };
}
it('StrictMode mount sends no create; explicit rapid double-click creates once and renders stored message without fake reply', async () => {
  const f = await mount();
  expect(f.server.creates).toBe(0);
  const start = screen.getByRole('button', { name: 'Start conversation' });
  await act(async () => {
    fireEvent.click(start);
    fireEvent.click(start);
  });
  const message = await screen.findByRole('textbox', { name: 'Your message' });
  expect(f.server.creates).toBe(1);
  expect(f.router.state.location.pathname).toBe('/help/conversations/' + ID);
  fireEvent.change(message, { target: { value: 'Synthetic parking issue' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save message' }));
  await waitFor(() =>
    expect(screen.getByText('Saved', { exact: true })).toBeVisible(),
  );
  expect(f.server.messages.size).toBe(1);
  expect(screen.getByText('Synthetic parking issue')).toBeVisible();
  expect(screen.getByRole('textbox')).toHaveValue('');
  expect(
    screen.queryByText(/Preparing a sample reply/),
  ).not.toBeInTheDocument();
});
it('locale and contact handoff preserve typed story, explicit language PATCH uses metadata and does not submit the story', async () => {
  const f = await mount();
  fireEvent.click(screen.getByRole('button', { name: 'Start conversation' }));
  const text = await screen.findByRole('textbox', { name: 'Your message' });
  fireEvent.change(text, { target: { value: 'Keep this ORIGINAL story' } });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Bahasa Indonesia' })),
  );
  expect(screen.getByRole('textbox')).toHaveValue('Keep this ORIGINAL story');
  expect(f.server.patches).toBe(0);
  expect(screen.getByRole('button', { name: 'Simpan pesan' })).toBeDisabled();
  fireEvent.click(
    screen.getByRole('button', {
      name: 'Gunakan Bahasa Indonesia untuk pesan baru',
    }),
  );
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Simpan pesan' })).toBeEnabled(),
  );
  expect(f.server.patches).toBe(1);
  expect(f.server.posts).toBe(0);
  await act(async () =>
    fireEvent.click(screen.getAllByRole('link', { name: 'Petugas' })[0]!),
  );
  expect(f.router.state.location.search).toBe('?topic=contact&lang=id');
  expect(screen.getByRole('textbox')).toHaveValue('Keep this ORIGINAL story');
  expect(screen.getByText('Cerita Anda tetap dibawa.')).toBeVisible();
});
it('reloading a known conversation reads authorized persisted history without creating a conversation', async () => {
  const f = await mount('/help/conversations/' + ID + '?lang=en');
  await screen.findByRole('textbox', { name: 'Your message' });
  expect(f.server.creates).toBe(0);
  expect(f.server.posts).toBe(0);
  expect(screen.getByRole('log')).toBeEmptyDOMElement();
  expect(
    screen.getByText(/Saved conversation history is loaded from the service/),
  ).toBeVisible();
});

it('shows request review only after a confirmed saved message, and hides it while editing or saving', async () => {
  const f = await mount(
    '/help/conversations/' + ID + '?topic=information&lang=en',
    'customer',
  );
  const input = await screen.findByRole('textbox', { name: 'Your message' });
  await waitFor(() => expect(input).toBeEnabled());
  expect(
    screen.queryByRole('button', { name: 'Review support request' }),
  ).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Save message' })).toBeDisabled();
  fireEvent.change(input, { target: { value: 'Synthetic support details' } });
  expect(
    screen.queryByRole('button', { name: 'Review support request' }),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Save message' }));
  await screen.findByText('Saved', { exact: true });
  expect(
    screen.getByRole('button', { name: 'Review support request' }),
  ).toBeVisible();
  expect(screen.getByText('Your message is saved')).toBeVisible();
  expect(f.server.posts).toBe(1);
  const freshInput = await screen.findByRole('textbox', {
    name: 'Your message',
  });
  await waitFor(() => expect(freshInput).toBeEnabled());
  fireEvent.change(freshInput, { target: { value: 'An unsaved addition' } });
  expect(
    screen.queryByRole('button', { name: 'Review support request' }),
  ).not.toBeInTheDocument();
  expect(f.server.posts).toBe(1);
});

it('restores original multilingual customer and staff text with server human mode after reload', async () => {
  const f = await mount(
    '/help/conversations/' + ID + '?lang=en',
    'anonymous',
    (server) => {
      server.mode = 'human';
      server.events = [
        {
          id: ID,
          sequence: 1,
          event_type: 'message_received',
          created_at: '2026-11-04T12:00:00Z',
          payload: {
            message_id: ID,
            body: 'Pesanan lounge saya belum ditemukan.',
            language: 'id',
            author_type: 'customer',
          },
        },
        {
          id: OTHER,
          sequence: 2,
          event_type: 'staff_message',
          created_at: '2026-11-04T12:01:00Z',
          payload: {
            message_id: OTHER,
            body: 'I have your original message. Please confirm the booking reference.',
            language: 'en',
            author_type: 'staff',
          },
        },
      ];
    },
  );
  expect(
    await screen.findByText('Pesanan lounge saya belum ditemukan.'),
  ).toBeVisible();
  expect(
    screen.getByText(
      'I have your original message. Please confirm the booking reference.',
    ),
  ).toBeVisible();
  expect(
    screen.getByText('A staff member is handling this conversation'),
  ).toBeVisible();
  expect(screen.getByText(/Support staff/)).toBeVisible();
  expect(f.server.posts).toBe(0);
  expect(f.server.creates).toBe(0);
  fireEvent.click(screen.getByRole('button', { name: 'Bahasa Indonesia' }));
  expect(
    screen.getByText(
      'I have your original message. Please confirm the booking reference.',
    ),
  ).toBeVisible();
  expect(
    screen.getByText('Pesanan lounge saya belum ditemukan.'),
  ).toBeVisible();
  expect(screen.getByRole('button', { name: 'Simpan pesan' })).toBeDisabled();
});
it('an ended conversation restores history but disables new sends and does not offer a new support request', async () => {
  const f = await mount(
    '/help/conversations/' + ID + '?lang=en',
    'customer',
    (server) => {
      server.mode = 'ended';
      server.events = [
        {
          id: ID,
          sequence: 1,
          event_type: 'message_received',
          created_at: '2026-11-04T12:00:00Z',
          payload: {
            message_id: ID,
            body: 'My earlier support request.',
            language: 'en',
            author_type: 'customer',
          },
        },
      ];
    },
  );
  expect(await screen.findByText('This conversation has ended')).toBeVisible();
  expect(screen.getByRole('textbox', { name: 'Your message' })).toBeDisabled();
  expect(
    screen.queryByRole('button', { name: 'Review support request' }),
  ).not.toBeInTheDocument();
  expect(f.server.posts).toBe(0);
});

it('offers email verification only while the saved customer history is ready', async () => {
  const f = await mount(
    '/help/conversations/' + ID + '?topic=contact&lang=en',
    'anonymous',
    (server) => {
      server.events = [
        {
          id: ID,
          sequence: 1,
          event_type: 'message_received',
          created_at: '2026-11-05T12:00:00Z',
          payload: {
            message_id: ID,
            author_type: 'customer',
            language: 'en',
            body: 'Keep my report for verification',
          },
        },
      ];
    },
  );
  expect(
    await screen.findByRole('link', { name: 'Continue to email verification' }),
  ).toBeVisible();
  const visibility = vi.spyOn(document, 'visibilityState', 'get');
  try {
    visibility.mockReturnValue('hidden');
    act(() => document.dispatchEvent(new Event('visibilitychange')));
    expect(
      screen.queryByRole('link', { name: 'Continue to email verification' }),
    ).not.toBeInTheDocument();
    visibility.mockReturnValue('visible');
    act(() => document.dispatchEvent(new Event('visibilitychange')));
    expect(
      await screen.findByRole('link', {
        name: 'Continue to email verification',
      }),
    ).toBeVisible();
  } finally {
    visibility.mockRestore();
  }
  expect(f.server.posts).toBe(0);
});

it('shows current-route recovery for an initial missing conversation instead of loading forever', async () => {
  await mount('/help/conversations/' + OTHER + '?lang=en');
  expect(
    await screen.findByRole('button', { name: 'Check conversation' }),
  ).toBeEnabled();
  expect(
    screen.queryByText('Checking your session and conversation…'),
  ).not.toBeInTheDocument();
});

it('erases a mounted private answer on source outage and exposes an explicit fresh-read recovery', async () => {
  const f = await mount(
    '/help/conversations/' + ID + '?lang=en',
    'customer',
    (server) => {
      server.events = [
        {
          id: ID,
          sequence: 1,
          event_type: 'ai_message',
          created_at: '2026-11-05T12:00:01Z',
          payload: {
            message_id: ID,
            author_type: 'ai',
            language: 'en',
            body: 'Private answer to erase',
          },
        },
      ];
    },
  );
  expect(await screen.findByText('Private answer to erase')).toBeVisible();
  f.server.override = async () => error(503, 'SOURCE_UNAVAILABLE');
  fireEvent.click(screen.getByRole('button', { name: 'Sources' }));
  const retry = await screen.findByRole('button', {
    name: 'Check conversation',
  });
  expect(screen.queryByText('Private answer to erase')).not.toBeInTheDocument();
  expect(
    screen.queryByText('Checking your session and conversation…'),
  ).not.toBeInTheDocument();
  f.server.override = null;
  fireEvent.click(retry);
  expect(await screen.findByText('Private answer to erase')).toBeVisible();
  expect(f.server.posts).toBe(0);
});

it('offers sources only for saved AI events, never auto-fetches and keeps the saved answer on citation503', async () => {
  const f = await mount(
    '/help/conversations/' + ID + '?lang=en',
    'anonymous',
    (server) => {
      server.events = [
        {
          id: ID,
          sequence: 1,
          event_type: 'message_received',
          created_at: '2026-11-05T12:00:00Z',
          payload: {
            message_id: ID,
            author_type: 'customer',
            language: 'en',
            body: 'Where can I wait?',
          },
        },
        {
          id: OTHER,
          sequence: 2,
          event_type: 'ai_message',
          created_at: '2026-11-05T12:00:01Z',
          payload: {
            message_id: OTHER,
            author_type: 'ai',
            language: 'en',
            body: 'The recorded public answer.',
          },
        },
      ];
    },
  );
  expect(await screen.findByText('The recorded public answer.')).toBeVisible();
  expect(screen.getAllByRole('button', { name: 'Sources' })).toHaveLength(1);
  expect(
    f.fetcher.mock.calls.some(([path]) => String(path).endsWith('/citations')),
  ).toBe(false);
  f.server.override = async () => error(503, 'AGENT_UNAVAILABLE');
  fireEvent.click(screen.getByRole('button', { name: 'Sources' }));
  await screen.findByText('The service is temporarily unavailable.');
  expect(screen.getByText('The recorded public answer.')).toBeVisible();
  expect(screen.getByText('Where can I wait?')).toBeVisible();
  expect(
    f.fetcher.mock.calls.filter(([path]) =>
      String(path).endsWith('/citations'),
    ),
  ).toHaveLength(1);
  expect(f.server.posts).toBe(0);
  expect(f.server.creates).toBe(0);
});
