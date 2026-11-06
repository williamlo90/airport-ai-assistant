import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { StrictMode } from 'react';
import { createMemoryRouter } from 'react-router';
import { afterEach, expect, it } from 'vitest';
import { fixture, json, error, ID, OTHER } from '../chat/fixture';
import { closedCase, CASE } from './detail-fixture';
import { App } from '../../src/app/App';
import { createBootstrap } from '../../src/app/config/publicSite';
import { createAppRoutes } from '../../src/app/routing/routes';
const owned: (() => void)[] = [];
afterEach(() => owned.splice(0).forEach((off) => off()));
async function mount(path = '/help/cases?lang=en', kind = 'customer') {
  const f = fixture();
  f.server.kind = kind;
  f.server.principal = kind === 'customer' ? OTHER : null;
  await f.auth.start();
  const server = {
    detail: {
      ...closedCase(),
      status: 'new' as const,
      next_actor: 'staff' as const,
      next_action: 'Review the report.',
      version: 1,
      close_reason: null,
      resolution: null,
      closed_at: null,
      history: closedCase().history.slice(0, 1),
    },
    posts: [] as string[],
    message: '',
    saved: false,
    lost: false,
    denied: false,
    listGets: 0,
  };
  f.server.override = async (url, init) => {
    if (url === '/api/v1/me')
      return json({
        session_kind: f.server.kind,
        principal_id: f.server.principal,
      });
    if (url.startsWith('/api/v1/cases?')) {
      server.listGets++;
      if (server.denied) return error(404, 'RESOURCE_NOT_FOUND');
      const status = new URL(url, 'https://test.invalid').searchParams.get(
        'status',
      );
      return json({
        items:
          status && status !== server.detail.status
            ? []
            : [{ ...server.detail, created_at: '2026-11-04T07:50:00Z' }],
        status,
        limit: 25,
        sort: 'created_asc',
        has_more: false,
        next_cursor: null,
        watermark: '2026-11-06T00:00:00Z',
        consistency: 'live_keyset',
      });
    }
    if (url.endsWith('/attachments'))
      return json([
        {
          id: ID,
          byte_size: 32,
          mime_type: 'application/pdf',
          scan_status: 'clean',
        },
      ]);
    if (init?.method === 'POST') {
      server.posts.push(url);
      const body = JSON.parse(String(init.body));
      if (url.endsWith('/information-previews')) {
        server.message = body.message;
        return json({
          ...body,
          case_id: CASE,
          preview_id: ID,
          expires_at: Math.floor(Date.now() / 1000) + 900,
          confirmation_token: 'private-token-never-visible',
        });
      }
      if (url.endsWith('/information')) {
        server.saved = true;
        server.detail = {
          ...server.detail,
          version: 2,
          history: [
            ...server.detail.history,
            {
              event_type: 'customer_information',
              previous_version: 1,
              resulting_version: 2,
              from_status: null,
              to_status: null,
              next_actor: null,
              next_action: null,
              close_reason: null,
              resolution: null,
              customer_message: server.message,
              created_at: '2026-11-06T00:00:00Z',
            },
          ],
        };
        if (server.lost) throw Error('lost response');
      }
    }
    if (url.includes('/information-receipts/') || url.endsWith('/information'))
      return server.saved
        ? json(
            {
              state: 'saved',
              case_id: CASE,
              preview_id: ID,
              message_id: OTHER,
              sequence: 2,
              case_version: 2,
            },
            init?.method === 'POST' ? 201 : 200,
          )
        : error(404, 'RESOURCE_NOT_FOUND');
    if (url.endsWith('/cases/' + CASE)) return json(server.detail);
    return error(404, 'RESOURCE_NOT_FOUND');
  };
  const bootstrap = createBootstrap('localhost', '');
  if (bootstrap.status !== 'configured') throw Error('fixture');
  const router = createMemoryRouter(
    createAppRoutes(bootstrap.site, undefined, [], f.auth),
    { initialEntries: [path] },
  );
  owned.push(() => {
    router.dispose();
    f.dispose();
  });
  render(
    <StrictMode>
      <App bootstrap={bootstrap} router={router} />
    </StrictMode>,
  );
  return { ...f, workflow: server, router };
}
it('opens the real My cases route, filters and navigates to the selected private case without a write', async () => {
  const f = await mount();
  const link = await screen.findByRole(
    'link',
    { name: 'Existing parking booking after a delay' },
    { timeout: 5000 },
  );
  expect(link).toHaveAttribute('href', `/help/cases/${CASE}?lang=en`);
  fireEvent.change(screen.getByLabelText('Case status'), {
    target: { value: 'closed' },
  });
  await screen.findByText('No cases found for this selection.');
  fireEvent.change(screen.getByLabelText('Case status'), {
    target: { value: '' },
  });
  fireEvent.click(
    await screen.findByRole('link', {
      name: 'Existing parking booking after a delay',
    }),
  );
  await screen.findByRole('heading', { name: 'Your case, at a glance' });
  expect(f.workflow.posts).toHaveLength(0);
  expect(
    await screen.findByRole('link', { name: 'Download attachment 1' }),
  ).toHaveAttribute('href', `/api/v1/attachments/${ID}/download`);
});
it('renders exact original review, requires a checkbox and shows recorded original text in history', async () => {
  const f = await mount(`/help/cases/${CASE}?lang=en`);
  const field = await screen.findByLabelText('Your additional information');
  fireEvent.change(field, {
    target: { value: '<script>literal quoted text</script>\nSecond line.' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Review message' }));
  await screen.findByRole('heading', { name: 'Review before sending' });
  const send = screen.getByRole('button', { name: 'Confirm and send' });
  expect(send).toBeDisabled();
  expect(screen.queryByText('private-token-never-visible')).toBeNull();
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(send);
  await screen.findByRole('heading', {
    name: 'Additional information recorded',
  });
  expect(
    screen.getByText('<script>literal quoted text</script> Second line.'),
  ).toBeInTheDocument();
  expect(
    f.workflow.posts.filter((p) => p.endsWith('/information')),
  ).toHaveLength(1);
  expect(f.workflow.detail.status).toBe('new');
});
it('a lost confirmation offers GET-only receipt recovery and never resends the message', async () => {
  const f = await mount(`/help/cases/${CASE}?lang=en`);
  f.workflow.lost = true;
  fireEvent.change(
    await screen.findByLabelText('Your additional information'),
    { target: { value: 'Original message' } },
  );
  fireEvent.click(screen.getByRole('button', { name: 'Review message' }));
  await screen.findByRole('heading', { name: 'Review before sending' });
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: 'Confirm and send' }));
  fireEvent.click(
    await screen.findByRole('button', { name: 'Check message receipt' }),
  );
  await screen.findByRole('heading', {
    name: 'Additional information recorded',
  });
  expect(
    f.workflow.posts.filter((p) => p.endsWith('/information')),
  ).toHaveLength(1);
});
it('anonymous visitors see verification instead of calling the private list endpoint', async () => {
  const f = await mount('/help/cases?lang=en', 'anonymous');
  await screen.findByText('Verify your email to view your support cases.');
  expect(screen.getByRole('link', { name: 'Verify email' })).toHaveAttribute(
    'href',
    '/help/verify?lang=en',
  );
  expect(f.workflow.listGets).toBe(0);
});
it('a current denied refresh removes every previously displayed list item', async () => {
  const f = await mount();
  await screen.findByRole('link', {
    name: 'Existing parking booking after a delay',
  });
  f.workflow.denied = true;
  fireEvent.click(screen.getByRole('button', { name: 'Refresh cases' }));
  await waitFor(() =>
    expect(
      screen.queryByRole('link', {
        name: 'Existing parking booking after a delay',
      }),
    ).toBeNull(),
  );
  await act(async () => {
    await Promise.resolve();
  });
  expect(f.workflow.posts).toHaveLength(0);
});
