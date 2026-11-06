import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { createBootstrap } from '../../src/app/config/publicSite';
import { createAppRoutes } from '../../src/app/routing/routes';
import { Component } from '../../src/dev/EnvironmentPreview';

function mount() {
  const bootstrap = createBootstrap('localhost', '');
  if (bootstrap.status !== 'configured') throw new Error('fixture');
  const router = createMemoryRouter(
    createAppRoutes(bootstrap.site, undefined, [
      { path: 'diagnostic', element: <Component /> },
    ]),
    { initialEntries: ['/diagnostic'] },
  );
  render(<RouterProvider router={router} />);
}
function secure() {
  vi.stubGlobal('location', { origin: 'https://localhost:8443' });
  vi.stubGlobal('isSecureContext', true);
}
afterEach(() => vi.unstubAllGlobals());
describe('explicit HTTPS diagnostic', () => {
  it('accepts logout cookie removal and confirms the anonymous session is revoked', async () => {
    secure();
    let cookie = '';
    let inspection = 0;
    vi.spyOn(document, 'cookie', 'get').mockImplementation(() => cookie);
    const anonymous = () =>
      new Response(
        JSON.stringify({ session_kind: 'anonymous', principal_id: null }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      );
    const send = vi.fn(async (url: string, options?: RequestInit) => {
      if (url === '/api/v1/me')
        return ++inspection === 2
          ? anonymous()
          : new Response(null, { status: 401 });
      if (url === '/api/v1/auth/sessions') {
        cookie = '__Host-airport_csrf=' + 'a'.repeat(43);
        return anonymous();
      }
      if (url === '/api/v1/auth/logout') {
        if (!new Headers(options?.headers).has('X-CSRF-Token'))
          return new Response(null, { status: 403 });
        cookie = '';
        return new Response(null, { status: 204 });
      }
      throw new Error('Unexpected diagnostic request');
    });
    vi.stubGlobal('fetch', send);
    mount();
    fireEvent.click(
      screen.getByRole('button', { name: 'Run connection check' }),
    );
    expect(
      await screen.findByText(/^PASS: secure browser origin/),
    ).toBeInTheDocument();
    expect(cookie).toBe('');
    expect(send).toHaveBeenCalledTimes(6);
  });
  it('does not run over HTTP or on mount', () => {
    const send = vi.fn();
    vi.stubGlobal('fetch', send);
    mount();
    expect(
      screen.getByRole('button', { name: 'Run connection check' }),
    ).toBeDisabled();
    expect(send).not.toHaveBeenCalled();
  });
  it('preserves an existing readable session proof without making requests', async () => {
    secure();
    vi.spyOn(document, 'cookie', 'get').mockReturnValue(
      '__Host-airport_csrf=' + 'a'.repeat(43),
    );
    const send = vi.fn();
    vi.stubGlobal('fetch', send);
    mount();
    fireEvent.click(
      screen.getByRole('button', { name: 'Run connection check' }),
    );
    expect(await screen.findByText(/Check incomplete/)).toBeInTheDocument();
    expect(send).not.toHaveBeenCalled();
  });
  it('preserves an HttpOnly existing session when readable proof is missing', async () => {
    secure();
    vi.spyOn(document, 'cookie', 'get').mockReturnValue('');
    const send = vi.fn(
      async () =>
        new Response(JSON.stringify({ session_kind: 'customer' }), {
          status: 200,
        }),
    );
    vi.stubGlobal('fetch', send);
    mount();
    fireEvent.click(
      screen.getByRole('button', { name: 'Run connection check' }),
    );
    expect(await screen.findByText(/Check incomplete/)).toBeInTheDocument();
    expect(send).toHaveBeenCalledTimes(1);
    expect(send.mock.calls[0]).toBeDefined();
  });
});
