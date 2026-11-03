import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';
import { StrictMode, useState } from 'react';
import { createMemoryRouter, Link } from 'react-router';
import { App } from '../../src/app/App';
import { createBootstrap } from '../../src/app/config/publicSite';
import { createAppRoutes } from '../../src/app/routing/routes';
import type { RouteAccess } from '../../src/app/routing/context';
import { useRouting } from '../../src/app/routing/context';
import { useUnsavedChanges } from '../../src/app/routing/draftState';
import { PageFrame } from '../../src/app/routing/pages';
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
      this.dispatchEvent(new Event('close'));
    },
  });
});
afterAll(() => {
  Reflect.deleteProperty(HTMLDialogElement.prototype, 'showModal');
  Reflect.deleteProperty(HTMLDialogElement.prototype, 'close');
});
function Editor() {
  const [text, setText] = useState('');
  const { switchLanguage } = useRouting();
  useUnsavedChanges(!!text, () => setText(''));
  return (
    <PageFrame>
      <label>
        Story
        <textarea value={text} onChange={(e) => setText(e.target.value)} />
      </label>
      <Link to="/">Leave</Link>
      <Link to="/edit?topic=contact">Handoff</Link>
      <Link to="/edit?topic=payment">Other topic</Link>
      <button onClick={switchLanguage}>Change language</button>
    </PageFrame>
  );
}
function mount(
  entries = ['/edit?topic=booking'],
  access: RouteAccess = { kind: 'unavailable' },
) {
  const bootstrap = createBootstrap('localhost', '');
  if (bootstrap.status !== 'configured') throw new Error('fixture');
  const router = createMemoryRouter(
    createAppRoutes(bootstrap.site, access, [
      { path: 'edit', element: <Editor /> },
      {
        path: 'broken',
        loader: () => {
          throw new Error('PRIVATE_INTERNAL_ERROR');
        },
        element: <p>Never shown</p>,
      },
    ]),
    { initialEntries: entries, initialIndex: entries.length - 1 },
  );
  render(
    <StrictMode>
      <App bootstrap={bootstrap} router={router} />
    </StrictMode>,
  );
  return router;
}
const dirty = () =>
  fireEvent.change(screen.getByRole('textbox', { name: 'Story' }), {
    target: { value: 'Please help with parking' },
  });
describe('routing integration', () => {
  it('cancel, Escape wiring and discard protect in-memory input', async () => {
    const router = mount();
    dirty();
    fireEvent.click(screen.getByRole('link', { name: 'Other topic' }));
    expect(await screen.findByRole('dialog')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Keep editing' }));
    expect(screen.getByRole('textbox')).toHaveValue('Please help with parking');
    expect(router.state.location.search).toBe('?topic=booking');
    fireEvent.click(screen.getByRole('link', { name: 'Other topic' }));
    fireEvent(
      screen.getByRole('dialog'),
      new Event('cancel', { cancelable: true }),
    );
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('link', { name: 'Other topic' }));
    await act(async () =>
      fireEvent.click(
        screen.getByRole('button', { name: 'Discard changes and leave' }),
      ),
    );
    expect(router.state.location.search).toBe('?topic=payment');
    expect(screen.getByRole('textbox')).toHaveValue('');
  });
  it('retains text through language and same-issue handoff', async () => {
    const router = mount();
    dirty();
    await act(async () =>
      fireEvent.click(screen.getByRole('button', { name: 'Change language' })),
    );
    expect(router.state.location.search).toBe('?topic=booking&lang=id');
    expect(screen.getByRole('textbox')).toHaveValue('Please help with parking');
    await act(async () =>
      fireEvent.click(screen.getByRole('link', { name: 'Handoff' })),
    );
    expect(screen.getByRole('textbox')).toHaveValue('Please help with parking');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
  it('blocks Back/forward and registers beforeunload only for dirty state', async () => {
    const router = mount(['/', '/edit?topic=booking']);
    const clean = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(clean);
    expect(clean.defaultPrevented).toBe(false);
    dirty();
    const unload = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(unload);
    expect(unload.defaultPrevented).toBe(true);
    await act(async () => router.navigate(-1));
    expect(await screen.findByRole('dialog')).toBeVisible();
    await act(async () =>
      fireEvent.click(
        screen.getByRole('button', { name: 'Discard changes and leave' }),
      ),
    );
    expect(router.state.location.pathname).toBe('/');
    await act(async () => router.navigate(1));
    expect(screen.getByRole('textbox')).toHaveValue('');
  });
  it('the brand link also uses the blocker', async () => {
    mount();
    dirty();
    fireEvent.click(
      screen.getByRole('link', { name: 'Westbridge Airport — AI Assistant' }),
    );
    expect(await screen.findByRole('dialog')).toBeVisible();
  });
  it.each([
    '/help/conversations/11111111-1111-4111-8111-111111111111',
    '/help/submissions/11111111-1111-4111-8111-111111111111',
    '/help/cases/11111111-1111-4111-8111-111111111111',
    '/staff/cases',
    '/staff/cases/11111111-1111-4111-8111-111111111111',
  ])('deep link fails closed until session is available: %s', (path) => {
    const router = mount([path + '?role=cs&tenant=other']);
    expect(
      screen.getByRole('heading', { name: 'Session verification required' }),
    ).toBeInTheDocument();
    expect(router.state.location.pathname).toBe(path);
  });
  it.each([
    '/not-a-real-page',
    '/help/cases/not-a-uuid',
    '/help/new?topic=invalid',
  ])('generic not-found for %s', (path) => {
    mount([path]);
    expect(
      screen.getByRole('heading', { name: 'Page not found' }),
    ).toBeInTheDocument();
    expect(screen.queryByText(path)).not.toBeInTheDocument();
  });
  it('preserves a draft pointer after a fresh router load without submitting', () => {
    const path = '/help/submissions/11111111-1111-4111-8111-111111111111';
    const router = mount([path], { kind: 'customer' });
    expect(router.state.location.pathname).toBe(path);
    expect(screen.getByRole('status')).toHaveTextContent(
      'no successful submission is being claimed',
    );
  });
  it('admin alone is not a case role', () => {
    mount(['/staff/cases'], { kind: 'staff', roles: ['administrator'] });
    expect(
      screen.getByRole('heading', { name: 'Session verification required' }),
    ).toBeInTheDocument();
  });
  it.each(['cs', 'supervisor'])(
    'trusted %s capability opens only the placeholder',
    (role) => {
      mount(['/staff/cases'], { kind: 'staff', roles: [role] });
      expect(
        screen.getByRole('heading', { name: 'Support cases' }),
      ).toBeInTheDocument();
      expect(screen.getByRole('status')).toHaveTextContent(
        'No request has been sent',
      );
    },
  );
  it('sanitizes route errors', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    try {
      mount(['/broken']);
      await waitFor(() =>
        expect(
          screen.getByRole('heading', { name: 'Page unavailable' }),
        ).toBeInTheDocument(),
      );
      expect(
        screen.queryByText(/PRIVATE_INTERNAL_ERROR/),
      ).not.toBeInTheDocument();
    } finally {
      spy.mockRestore();
    }
  });
});
