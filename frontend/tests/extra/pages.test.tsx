import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { createMemoryRouter } from 'react-router';
import { afterEach, expect, it } from 'vitest';
import { App } from '../../src/app/App';
import { createAppRoutes } from '../../src/app/routing/routes';
import { createBootstrap } from '../../src/app/config/publicSite';
import { RatingPanel } from '../../src/features/feedback/Rating';
import { RatingStore } from '../../src/features/feedback/store';
import { AdminPanel } from '../../src/features/admin/AdminPage';
import { adminFixture, OTHER_PERSON, QUEUE } from './admin-fixture';
import { fixture, json, ID, OTHER } from '../chat/fixture';
import type { AuthCoordinator } from '../../src/app/auth/coordinator';
import type { ReactNode } from 'react';
const owned: (() => void)[] = [];
afterEach(() => owned.splice(0).forEach((f) => f()));
function mount(element: ReactNode, auth: AuthCoordinator, lang = 'en') {
  const b = createBootstrap('localhost', '');
  if (b.status !== 'configured') throw Error('fixture');
  const router = createMemoryRouter(
    createAppRoutes(b.site, undefined, [{ path: 'test-extra', element }], auth),
    { initialEntries: ['/test-extra?lang=' + lang] },
  );
  owned.push(() => router.dispose());
  render(<App bootstrap={b} router={router} />);
}
async function feedback() {
  const f = fixture();
  await f.auth.start();
  const store = new RatingStore(f.auth),
    off = store.connect();
  let value: unknown = null;
  const calls: unknown[] = [];
  f.server.override = async (_, init) => {
    if (init?.method === 'POST') {
      const body = JSON.parse(String(init.body));
      calls.push(body);
      value = {
        id: OTHER,
        score: body.score,
        comment: body.comment,
        version: 1,
        created_at: '2026-11-01T00:00:00Z',
        updated_at: '2026-11-01T00:00:00Z',
      };
    }
    return json({
      target_type: 'conversation',
      target_id: ID,
      eligible: true,
      rating: value,
    });
  };
  await store.open({ kind: 'conversation', id: ID });
  owned.push(() => {
    off();
    f.dispose();
  });
  return { ...f, store, calls };
}
it('starts with no score selected, validates, saves explicit selection, optional skip never writes', async () => {
  const f = await feedback();
  mount(<RatingPanel store={f.store} />, f.auth);
  const radios = screen.getAllByRole('radio');
  expect(radios).toHaveLength(5);
  expect(radios.every((r) => !(r as HTMLInputElement).checked)).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Save rating' }));
  expect(screen.getByRole('alert')).toHaveTextContent('Choose a rating');
  expect(f.calls).toHaveLength(0);
  fireEvent.click(screen.getByRole('radio', { name: '4' }));
  fireEvent.change(
    screen.getByRole('textbox', { name: 'Comment (optional)' }),
    { target: { value: 'Clear explanation.' } },
  );
  fireEvent.click(screen.getByRole('button', { name: 'Save rating' }));
  await screen.findByText('Your rating was saved.');
  expect(f.calls).toEqual([
    { score: 4, comment: 'Clear explanation.', expected_version: 0 },
  ]);
  fireEvent.click(screen.getByRole('button', { name: 'Skip for now' }));
  expect(f.calls).toHaveLength(1);
});
it('Indonesian labels remain separate from the original comment', async () => {
  const f = await feedback();
  mount(<RatingPanel store={f.store} />, f.auth, 'id');
  expect(
    screen.getByRole('group', { name: 'Nilai 1 sampai 5' }),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Lewati dulu' }));
  expect(f.calls).toHaveLength(0);
});
it('admin review shows before and after and writes only after explicit confirmation', async () => {
  const f = await adminFixture();
  owned.push(() => f.dispose());
  await f.admin.refresh();
  await f.admin.choose(OTHER_PERSON);
  mount(<AdminPanel store={f.admin} />, f.auth);
  fireEvent.click(screen.getByRole('checkbox', { name: 'Membership active' }));
  fireEvent.click(screen.getByRole('button', { name: 'Review changes' }));
  expect(
    screen.getByRole('region', { name: 'Review access changes' }),
  ).toHaveTextContent('Active');
  expect(
    screen.getByRole('region', { name: 'Review access changes' }),
  ).toHaveTextContent('Inactive');
  expect(f.calls.filter((c) => c.method === 'PATCH')).toHaveLength(0);
  fireEvent.click(screen.getByRole('button', { name: 'Save access changes' }));
  await screen.findByText('Access changes were saved.');
  expect(f.calls.filter((c) => c.method === 'PATCH')).toHaveLength(1);
});
it('unknown admin response clears edit fields, only GET recovery and no receipt claim', async () => {
  const f = await adminFixture();
  owned.push(() => f.dispose());
  await f.admin.refresh();
  await f.admin.choose(OTHER_PERSON);
  f.access.lost = true;
  mount(<AdminPanel store={f.admin} />, f.auth);
  fireEvent.click(screen.getByRole('checkbox', { name: 'Membership active' }));
  fireEvent.click(screen.getByRole('button', { name: 'Review changes' }));
  fireEvent.click(screen.getByRole('button', { name: 'Save access changes' }));
  await screen.findByRole('button', { name: 'Read current access' });
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Read current access' }));
  await screen.findByText(
    'Current access loaded. This is not a receipt for the earlier request.',
  );
  expect(f.calls.filter((c) => c.method === 'PATCH')).toHaveLength(1);
});
it('source hold purges displayed private comment and explicit recovery restores current data', async () => {
  const f = await feedback();
  await f.store.save(4, 'Private customer comment');
  mount(<RatingPanel store={f.store} />, f.auth);
  expect(screen.getByRole('textbox')).toHaveValue('Private customer comment');
  const original = f.server.override!;
  f.server.override = async () =>
    new Response(
      JSON.stringify({
        error: {
          code: 'SOURCE_UNAVAILABLE',
          message: 'hidden',
          request_id: ID,
          retryable: false,
        },
      }),
      { status: 503, headers: { 'Content-Type': 'application/json' } },
    );
  await act(() => f.store.recover());
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  expect(
    screen.queryByText('Private customer comment'),
  ).not.toBeInTheDocument();
  f.server.override = original;
  fireEvent.click(screen.getByRole('button', { name: 'Check current rating' }));
  await waitFor(() =>
    expect(screen.getByRole('textbox')).toHaveValue('Private customer comment'),
  );
  expect(f.calls).toHaveLength(1);
});
it('admin exact role review can remove a queue without adding hidden scopes', async () => {
  const f = await adminFixture();
  owned.push(() => f.dispose());
  await f.admin.refresh();
  await f.admin.choose(OTHER_PERSON);
  mount(<AdminPanel store={f.admin} />, f.auth);
  fireEvent.click(screen.getByRole('checkbox', { name: 'Parking support' }));
  fireEvent.click(screen.getByRole('button', { name: 'Review changes' }));
  expect(f.admin.getState().review?.after.queue_ids).toEqual([]);
  expect(f.admin.getState().review?.before.queue_ids).toEqual([QUEUE]);
  fireEvent.click(screen.getByRole('button', { name: 'Back to editing' }));
  expect(
    screen.getByRole('checkbox', { name: 'Parking support' }),
  ).not.toBeChecked();
  expect(f.calls.filter((c) => c.method === 'PATCH')).toHaveLength(0);
});

it('actual lazy /admin route uses its connected StaffProvider and selected membership', async () => {
  const f = await adminFixture();
  owned.push(() => f.dispose());
  const b = createBootstrap('localhost', '');
  if (b.status !== 'configured') throw Error('fixture');
  const router = createMemoryRouter(
    createAppRoutes(b.site, undefined, [], f.auth),
    { initialEntries: ['/admin?lang=en'] },
  );
  owned.push(() => router.dispose());
  render(<App bootstrap={b} router={router} />);
  fireEvent.click(
    await screen.findByRole('button', { name: 'Load staff accounts' }),
  );
  await screen.findByText('Morgan Reed');
  fireEvent.click(screen.getByRole('button', { name: 'View access' }));
  await screen.findByRole('checkbox', { name: 'Membership active' });
  expect(f.calls.at(-1)?.path).toContain(
    '/admin/staff/' + OTHER_PERSON + '/memberships?membership_id=',
  );
  expect(f.calls.filter((c) => c.method === 'PATCH')).toHaveLength(0);
});
it('actual /admin route denies a current CS membership rather than borrowing an admin role', async () => {
  const f = await adminFixture();
  owned.push(() => f.dispose());
  f.server.memberships[0] = { ...f.server.memberships[0]!, roles: ['cs'] };
  await f.auth.recover();
  const b = createBootstrap('localhost', '');
  if (b.status !== 'configured') throw Error('fixture');
  const router = createMemoryRouter(
    createAppRoutes(b.site, undefined, [], f.auth),
    { initialEntries: ['/admin?lang=en'] },
  );
  owned.push(() => router.dispose());
  render(<App bootstrap={b} router={router} />);
  await screen.findByText('Choose a membership with administrator access.');
  expect(
    screen.queryByRole('button', { name: 'Load staff accounts' }),
  ).not.toBeInTheDocument();
  expect(f.calls).toHaveLength(0);
});
