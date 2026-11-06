import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { useState } from 'react';
import { createMemoryRouter } from 'react-router';
import { AuthCoordinator } from '../../src/app/auth/coordinator';
import { App } from '../../src/app/App';
import { createBootstrap } from '../../src/app/config/publicSite';
import { createAppRoutes } from '../../src/app/routing/routes';
import { RequireAccess } from '../../src/app/routing/pages';
const id = '11111111-1111-4111-8111-111111111111';
const other = '22222222-2222-4222-8222-222222222222';
const owned: AuthCoordinator[] = [];
afterEach(() => owned.splice(0).forEach((a) => a.dispose()));
function Editor() {
  const [text, setText] = useState('');
  return (
    <label>
      Private note
      <input value={text} onChange={(e) => setText(e.target.value)} />
    </label>
  );
}
function mount(kind: 'customer' | 'staff' = 'customer', roles = ['cs']) {
  let principal = id;
  let proof = 'p'.repeat(43);
  let block: Promise<void> | null = null;
  const auth = new AuthCoordinator({
    readProof: () => proof,
    fetch: async (input) => {
      if (block) await block;
      return new Response(
        JSON.stringify(
          String(input).endsWith('/staff/me')
            ? {
                session_kind: 'staff',
                principal_id: principal,
                memberships: [
                  {
                    id: other,
                    organization_id: id,
                    version: 1,
                    roles,
                    queue_ids: [id],
                  },
                ],
              }
            : { session_kind: kind, principal_id: principal },
        ),
        { headers: { 'Content-Type': 'application/json' } },
      );
    },
  });
  owned.push(auth);
  const bootstrap = createBootstrap('localhost', '');
  if (bootstrap.status !== 'configured') throw Error('fixture');
  const router = createMemoryRouter(
    createAppRoutes(
      bootstrap.site,
      { kind: 'staff', roles: ['supervisor'] },
      [
        {
          path: 'private-editor',
          element: (
            <RequireAccess staff={kind === 'staff'}>
              <Editor />
            </RequireAccess>
          ),
        },
      ],
      auth,
    ),
    { initialEntries: ['/private-editor?lang=en&role=supervisor'] },
  );
  render(<App bootstrap={bootstrap} router={router} />);
  return {
    auth,
    swap() {
      principal = other;
      proof = 'q'.repeat(43);
    },
    block(value: Promise<void> | null) {
      block = value;
    },
  };
}
it('live coordinator overrides injected/URL roles and admin-only cannot read cases', async () => {
  const f = mount('staff', ['admin']);
  await act(() => f.auth.start());
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  expect(screen.getByText('Session verification required')).toBeVisible();
});
it('holds private DOM during recheck and resets component input across account change', async () => {
  const f = mount();
  await act(() => f.auth.start());
  fireEvent.change(screen.getByRole('textbox'), {
    target: { value: 'ACCOUNT A PRIVATE' },
  });
  let release!: () => void;
  f.block(
    new Promise<void>((r) => {
      release = r;
    }),
  );
  f.swap();
  let pending!: Promise<unknown>;
  act(() => {
    pending = f.auth.recheck();
  });
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  expect(
    screen.queryByDisplayValue('ACCOUNT A PRIVATE'),
  ).not.toBeInTheDocument();
  f.block(null);
  await act(async () => {
    release();
    await pending;
  });
  expect(screen.getByRole('textbox')).toHaveValue('');
});
