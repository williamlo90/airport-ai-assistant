/** Opt-in actual HTTPS API + React slice, isolated cookie jar; never a native browser proof. */
import { render, screen } from '@testing-library/react';
import { createMemoryRouter } from 'react-router';
import { expect, it } from 'vitest';
import { AuthCoordinator } from '../../src/app/auth/coordinator';
import { App } from '../../src/app/App';
import { createBootstrap } from '../../src/app/config/publicSite';
import { createAppRoutes } from '../../src/app/routing/routes';
const suiteEnv = (
  globalThis as unknown as {
    process: { env: Record<string, string | undefined> };
  }
).process.env;
it.skipIf(suiteEnv.RUN_STAFF_LIVE !== '1')(
  'reads real scoped queue and detail through React with temporary staff identity',
  async () => {
    const cookies = new Map<string, string>(),
      nativeFetch = globalThis.fetch;
    const adapter: typeof fetch = async (input, init) => {
      const headers = new Headers(init?.headers);
      headers.set('Origin', 'https://localhost:8443');
      if (cookies.size)
        headers.set(
          'Cookie',
          [...cookies].map(([k, v]) => k + '=' + v).join('; '),
        );
      const result = await nativeFetch(
        'https://localhost:8443' + String(input),
        { ...init, headers },
      );
      for (const raw of result.headers.getSetCookie()) {
        const pair = raw.split(';', 1)[0]!,
          split = pair.indexOf('=');
        cookies.set(pair.slice(0, split), pair.slice(split + 1));
      }
      return result;
    };
    const auth = new AuthCoordinator({
      fetch: adapter,
      readProof: () => cookies.get('__Host-airport_csrf') ?? null,
    });
    try {
      await auth.start();
      expect(auth.getState().phase).toBe('guest');
      await auth.change({ kind: 'bootstrap' });
      await auth.change({
        kind: 'staff-login',
        contact: suiteEnv.STAFF_LIVE_CONTACT!,
        password: suiteEnv.STAFF_LIVE_PASSWORD!,
      });
      expect(
        auth.getState().phase === 'ready' &&
          auth.capture().principalId === suiteEnv.STAFF_LIVE_PRINCIPAL,
      ).toBe(true);
      const bootstrap = createBootstrap('localhost', '');
      if (bootstrap.status !== 'configured') throw Error('fixture');
      const router = createMemoryRouter(
        createAppRoutes(bootstrap.site, undefined, [], auth),
        { initialEntries: ['/staff/cases?lang=en'] },
      );
      const view = render(<App bootstrap={bootstrap} router={router} />);
      await screen.findByRole(
        'button',
        { name: 'Next page' },
        { timeout: 20000 },
      );
      expect(document.body.textContent?.includes('Cases unavailable')).toBe(
        false,
      );
      await router.navigate(
        '/staff/cases/' + suiteEnv.STAFF_LIVE_CASE + '?lang=en',
      );
      await screen.findByRole(
        'heading',
        { name: 'Customer report' },
        { timeout: 20000 },
      );
      expect(
        document
          .querySelector('[aria-label="Case details"]')
          ?.textContent?.includes(
            'CASE-' + suiteEnv.STAFF_LIVE_CASE!.replaceAll('-', ''),
          ),
      ).toBe(true);
      expect(
        screen.queryByRole('button', { name: 'Claim case' }),
      ).not.toBeInTheDocument();
      view.unmount();
      router.dispose();
    } finally {
      auth.dispose();
      cookies.clear();
    }
  },
  60000,
);
