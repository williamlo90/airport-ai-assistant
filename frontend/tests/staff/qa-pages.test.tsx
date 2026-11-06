import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { createMemoryRouter } from 'react-router';
import { afterAll, afterEach, beforeAll, expect, it, vi } from 'vitest';
import { App } from '../../src/app/App';
import { createBootstrap } from '../../src/app/config/publicSite';
import { createAppRoutes } from '../../src/app/routing/routes';
import { StaffContext } from '../../src/features/staff/context';
import {
  StaffLogin,
  StaffWorkspace,
} from '../../src/features/staff/StaffPages';
import {
  staffFixture,
  member,
  deferred,
  error,
  CONTACT,
  PASSWORD,
  OTHER_PERSON,
  MEMBER,
  OTHER_MEMBER,
  OTHER_ORG,
  OTHER_QUEUE,
} from './qa-fixture';
import type { Kind } from './qa-fixture';

const owned: ReturnType<typeof staffFixture>[] = [];
const originalShow = Object.getOwnPropertyDescriptor(
  HTMLDialogElement.prototype,
  'showModal',
);
const originalClose = Object.getOwnPropertyDescriptor(
  HTMLDialogElement.prototype,
  'close',
);
beforeAll(() => {
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', {
    configurable: true,
    value: function (this: HTMLDialogElement) {
      this.setAttribute('open', '');
    },
  });
  Object.defineProperty(HTMLDialogElement.prototype, 'close', {
    configurable: true,
    value: function (this: HTMLDialogElement) {
      this.removeAttribute('open');
    },
  });
});
afterAll(() => {
  if (originalShow)
    Object.defineProperty(
      HTMLDialogElement.prototype,
      'showModal',
      originalShow,
    );
  else Reflect.deleteProperty(HTMLDialogElement.prototype, 'showModal');
  if (originalClose)
    Object.defineProperty(HTMLDialogElement.prototype, 'close', originalClose);
  else Reflect.deleteProperty(HTMLDialogElement.prototype, 'close');
});
afterEach(() => {
  vi.restoreAllMocks();
  owned.splice(0).forEach((f) => f.dispose());
});

async function mount(
  kind: Kind = 'anonymous',
  workspace = false,
  setup?: (f: ReturnType<typeof staffFixture>) => void,
) {
  const f = staffFixture();
  owned.push(f);
  setup?.(f);
  await f.ready(kind);
  const bootstrap = createBootstrap('localhost', '');
  if (bootstrap.status !== 'configured') throw Error('fixture');
  const router = createMemoryRouter(
    createAppRoutes(
      bootstrap.site,
      undefined,
      [
        {
          path: 'qa-staff',
          element: (
            <StaffContext value={f.staff}>
              {workspace ? <StaffWorkspace /> : <StaffLogin />}
            </StaffContext>
          ),
        },
      ],
      f.auth,
    ),
    { initialEntries: ['/qa-staff?lang=en'] },
  );
  render(<App bootstrap={bootstrap} router={router} />);
  await screen.findByRole('heading', {
    name: workspace ? 'Staff workspace' : 'Staff sign-in',
  });
  return { ...f, router };
}

it('guest staff page only prepares an anonymous session after its explicit button and never submits credentials on mount', async () => {
  const f = await mount('guest');
  expect(f.posts('/api/v1/auth/sessions')).toHaveLength(0);
  expect(screen.queryByLabelText('Password')).not.toBeInTheDocument();
  fireEvent.click(
    screen.getByRole('button', { name: 'Prepare staff sign-in' }),
  );
  await screen.findByLabelText('Password');
  expect(f.posts('/api/v1/auth/sessions')).toHaveLength(1);
  expect(f.posts('/api/v1/staff/auth/login')).toHaveLength(0);
});

it('customer staff-login gate and Stay signed in keep the legitimate customer session without any auth mutation', async () => {
  const f = await mount('customer');
  const owner = f.auth.capture();
  expect(screen.queryByLabelText('Password')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Sign out' }));
  const dialog = await screen.findByRole('dialog', {
    name: 'Sign out of this account?',
  });
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Stay signed in' }),
  );
  expect(f.auth.isCurrent(owner)).toBe(true);
  expect(
    f.fetcher.mock.calls.filter(([, init]) => init?.method === 'POST'),
  ).toHaveLength(0);
});

it('pending and rejected login clear the DOM password, preserve its submitted bytes, and never persist it as staff state', async () => {
  const f = await mount();
  const delayed = deferred();
  const storageWrites = vi.spyOn(Storage.prototype, 'setItem');
  f.server.handler = (path) =>
    path.endsWith('/staff/auth/login') ? delayed.promise : null;
  fireEvent.change(screen.getByLabelText('Staff email'), {
    target: { value: CONTACT },
  });
  const password = screen.getByLabelText('Password') as HTMLInputElement;
  expect(password.type).toBe('password');
  fireEvent.change(password, { target: { value: PASSWORD } });
  fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
  await waitFor(() =>
    expect(f.posts('/api/v1/staff/auth/login')).toHaveLength(1),
  );
  expect(password.value).toBe('');
  expect(JSON.stringify(f.staff.getState())).not.toContain(PASSWORD);
  const body = JSON.parse(
    String(f.posts('/api/v1/staff/auth/login')[0]?.[1]?.body),
  );
  expect(body).toEqual({ contact: CONTACT, password: PASSWORD });
  await act(async () => {
    delayed.resolve(error(401, 'STAFF_LOGIN_FAILED'));
  });
  await waitFor(() =>
    expect(screen.getByLabelText('Password')).toHaveValue(''),
  );
  expect(screen.getByRole('alert')).not.toHaveTextContent(
    'DO NOT DISPLAY RAW SERVER TEXT',
  );
  expect(JSON.stringify(f.staff.getState())).not.toContain(PASSWORD);
  expect(
    storageWrites.mock.calls.some((args) =>
      JSON.stringify(args).includes(PASSWORD),
    ),
  ).toBe(false);
  expect(f.router.state.location.search).toBe('?lang=en');
  expect(f.posts('/api/v1/staff/auth/login')).toHaveLength(1);
});

it('workspace navigation is scoped to the selected membership and never fetches cases for an administrative organization', async () => {
  const f = await mount('staff', true, (value) => {
    value.server.memberships = [
      { ...member(), roles: ['admin'] },
      {
        id: OTHER_MEMBER,
        organization_id: OTHER_ORG,
        version: 1,
        roles: ['cs'],
        queue_ids: [OTHER_QUEUE],
      },
    ];
  });
  const selection = screen.getByLabelText('Organization access');
  expect(selection).toHaveValue('');
  expect(screen.queryByRole('link', { name: 'Cases' })).not.toBeInTheDocument();
  fireEvent.change(selection, { target: { value: MEMBER } });
  expect(screen.getByText('Administrator')).toBeInTheDocument();
  expect(screen.queryByRole('link', { name: 'Cases' })).not.toBeInTheDocument();
  fireEvent.change(selection, { target: { value: OTHER_MEMBER } });
  expect(screen.getByRole('link', { name: 'Cases' })).toHaveAttribute(
    'href',
    '/staff/cases?lang=en',
  );
  expect(f.staff.captureScope()?.membership.organization_id).toBe(OTHER_ORG);
  expect(
    f.fetcher.mock.calls.some(([path]) => String(path).includes('/cases')),
  ).toBe(false);
});

it('a stale sign-out confirmation cannot log out a replacement customer account', async () => {
  const f = await mount('customer');
  fireEvent.click(screen.getByRole('button', { name: 'Sign out' }));
  const dialog = await screen.findByRole('dialog', {
    name: 'Sign out of this account?',
  });
  const staleAction = within(dialog).getByRole('button', {
    name: 'Sign out of account',
  });
  f.server.principal = OTHER_PERSON;
  f.server.proof = 'b'.repeat(43);
  await act(() => f.auth.recheck());
  fireEvent.click(staleAction);
  expect(f.posts('/api/v1/auth/logout')).toHaveLength(0);
  expect(f.auth.capture().principalId).toBe(OTHER_PERSON);
});

it('proof rotation before opening sign-out recovers the current session without an uncaught stale capture or logout', async () => {
  const f = await mount('customer');
  f.server.proof = 'b'.repeat(43);
  expect(() =>
    fireEvent.click(screen.getByRole('button', { name: 'Sign out' })),
  ).not.toThrow();
  await waitFor(() => expect(f.auth.capture().csrfProof).toBe('b'.repeat(43)));
  expect(
    screen.queryByRole('dialog', { name: 'Sign out of this account?' }),
  ).not.toBeInTheDocument();
  expect(f.posts('/api/v1/auth/logout')).toHaveLength(0);
});
