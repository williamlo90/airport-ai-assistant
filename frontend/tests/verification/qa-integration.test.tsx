import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { expect, it } from 'vitest';
import { createMemoryRouter } from 'react-router';
import { App } from '../../src/app/App';
import { createBootstrap } from '../../src/app/config/publicSite';
import { createAppRoutes } from '../../src/app/routing/routes';
import { verificationFixture, ID } from './fixture';
import { VerificationContext } from '../../src/features/verification/context';
import { VerificationPage } from '../../src/features/verification/VerificationPage';

it('lost new-conversation response after verified review is not described as failed verification', async () => {
  const f = verificationFixture();
  let router: ReturnType<typeof createMemoryRouter> | undefined;
  try {
    await f.auth.start();
    await f.store.open(ID);
    f.store.input({ body: 'reviewed original report', service: '' });
    await f.verify.request('synthetic@example.com');
    await f.verify.verify('12345678');
    f.server.loseCreate = true;
    const bootstrap = createBootstrap('localhost', '');
    if (bootstrap.status !== 'configured') throw new Error('fixture');
    router = createMemoryRouter(
      createAppRoutes(
        bootstrap.site,
        undefined,
        [
          {
            path: 'qa-verify',
            element: (
              <VerificationContext value={f.verify}>
                <VerificationPage />
              </VerificationContext>
            ),
          },
        ],
        f.auth,
      ),
      { initialEntries: ['/qa-verify?lang=en'] },
    );
    render(<App bootstrap={bootstrap} router={router} />);
    await act(async () => {
      fireEvent.click(
        screen.getByRole('button', { name: 'Continue with reviewed report' }),
      );
    });
    expect(f.verify.getState().contactVerified).toBe(true);
    expect(f.server.creates).toBe(1);
    expect(f.server.posts).toBe(0);
    expect(
      screen.queryByText(/Verification has not been confirmed/),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Continue with reviewed report' }),
    ).not.toBeInTheDocument();
  } finally {
    router?.dispose();
    f.dispose();
  }
});

it('actual chat entry retains unsent report/email across recheck and resumes only after explicit review', async () => {
  const f = verificationFixture();
  let router: ReturnType<typeof createMemoryRouter> | undefined;
  try {
    await f.auth.start();
    const bootstrap = createBootstrap('localhost', '');
    if (bootstrap.status !== 'configured') throw new Error('fixture');
    router = createMemoryRouter(
      createAppRoutes(bootstrap.site, undefined, [], f.auth),
      {
        initialEntries: [
          '/help/conversations/' + ID + '?topic=contact&lang=en',
        ],
      },
    );
    render(<App bootstrap={bootstrap} router={router} />);
    const message = await screen.findByRole('textbox', {
      name: 'Your message',
    });
    await waitFor(() => expect(message).toBeEnabled());
    fireEvent.change(message, {
      target: { value: 'The actual source story, before a code request' },
    });
    await act(async () => {
      fireEvent.click(
        screen.getByRole('link', { name: 'Continue to email verification' }),
      );
    });
    const contact = await screen.findByRole('textbox', {
      name: 'Email address',
    });
    fireEvent.change(contact, { target: { value: 'synthetic@example.com' } });
    await act(async () => {
      await f.auth.recheck();
    });
    expect(
      await screen.findByRole('textbox', { name: 'Email address' }),
    ).toHaveValue('synthetic@example.com');
    expect(f.scenario.requests).toBe(0);
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Request a code' }));
    });
    const code = await screen.findByRole('textbox', { name: '8-digit code' });
    fireEvent.change(code, { target: { value: '12345678' } });
    await act(async () => {
      await f.auth.recheck();
    });
    expect(
      await screen.findByRole('textbox', { name: '8-digit code' }),
    ).toHaveValue('');
    fireEvent.change(screen.getByRole('textbox', { name: '8-digit code' }), {
      target: { value: '12345678' },
    });
    await act(async () => {
      fireEvent.click(
        screen.getByRole('button', { name: 'Verify and continue' }),
      );
    });
    const report = await screen.findByRole('textbox', { name: 'Your report' });
    expect(report).toHaveValue(
      'The actual source story, before a code request',
    );
    expect(screen.getByText('Email verified for this session')).toBeVisible();
    expect(f.server.creates).toBe(0);
    expect(f.server.posts).toBe(0);
    await act(async () => {
      fireEvent.click(
        screen.getByRole('button', { name: 'Continue with reviewed report' }),
      );
    });
    await waitFor(() =>
      expect(screen.getByRole('textbox', { name: 'Your message' })).toHaveValue(
        'The actual source story, before a code request',
      ),
    );
    expect(f.server.creates).toBe(1);
    expect(f.server.posts).toBe(0);
  } finally {
    router?.dispose();
    f.dispose();
  }
});
