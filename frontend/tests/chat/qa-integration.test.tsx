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
import { ChatPage } from '../../src/features/chat/ChatPage';
import { ChatContext } from '../../src/features/chat/context';
import { fixture, ID } from './fixture';

function mount(f: ReturnType<typeof fixture>, existing = false) {
  const bootstrap = createBootstrap('localhost', '');
  if (bootstrap.status !== 'configured') throw new Error('fixture');
  const router = createMemoryRouter(
    createAppRoutes(
      bootstrap.site,
      undefined,
      [
        {
          path: 'qa-chat/:conversationId?',
          element: (
            <ChatContext value={f.store}>
              <ChatPage />
            </ChatContext>
          ),
        },
      ],
      f.auth,
    ),
    {
      initialEntries: [
        `/qa-chat${existing ? '/' + ID : ''}?topic=booking&lang=en`,
      ],
    },
  );
  render(<App bootstrap={bootstrap} router={router} />);
  return router;
}

it('unknown-create explanation remains visible after a same-session recheck clears errors', async () => {
  const f = fixture();
  let router: ReturnType<typeof mount> | undefined;
  try {
    await f.auth.start();
    f.server.loseCreate = true;
    await f.store.create('en');
    router = mount(f);
    expect(
      screen.getByText(/The conversation may have been created/),
    ).toBeVisible();
    await act(async () => {
      await f.auth.recheck();
    });
    expect(f.store.getState().error).toBeNull();
    expect(
      screen.getByText(/The conversation may have been created/),
    ).toBeVisible();
    expect(
      screen.queryByRole('button', { name: 'Start conversation' }),
    ).not.toBeInTheDocument();
    expect(f.server.creates).toBe(1);
  } finally {
    router?.dispose();
    f.dispose();
  }
});

it('form submits exact current body/service and marks received as Saved without an AI reply', async () => {
  const f = fixture();
  let router: ReturnType<typeof mount> | undefined;
  try {
    await f.auth.start();
    router = mount(f, true);
    await waitFor(() =>
      expect(
        screen.getByRole('textbox', { name: 'Your message' }),
      ).toBeEnabled(),
    );
    fireEvent.click(
      screen.getByText('Choose a service (optional)', { exact: true }),
    );
    fireEvent.change(
      screen.getByRole('combobox', { name: 'Service (optional)' }),
      { target: { value: 'lounge' } },
    );
    fireEvent.change(screen.getByRole('textbox', { name: 'Your message' }), {
      target: { value: 'Exact original words' },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Save message' }));
    });
    await waitFor(() => expect(screen.getByText('Saved')).toBeVisible());
    expect([...f.server.messages.values()][0]?.body).toBe(
      'lounge\n\nExact original words',
    );
    expect(screen.getByRole('textbox', { name: 'Your message' })).toHaveValue(
      '',
    );
    expect(f.server.posts).toBe(1);
    expect(f.server.creates).toBe(0);
    expect(screen.getByText(/Saved means the message is stored/)).toBeVisible();
  } finally {
    router?.dispose();
    f.dispose();
  }
});
