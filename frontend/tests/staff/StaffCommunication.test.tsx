import { StrictMode } from 'react';
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { StaffCommunication } from '../../src/features/staff/StaffCommunication';
import { RoutingContext } from '../../src/app/routing/context';
import { DraftContext, DraftRegistry } from '../../src/app/routing/draftState';
import { createBootstrap } from '../../src/app/config/publicSite';
import { createMemoryRouter } from 'react-router';
import { App } from '../../src/app/App';
import { createAppRoutes } from '../../src/app/routing/routes';
import { caseId, denied, MEMBER } from '../../src/dev/staff-fixtures';
import {
  communicationFixture,
  enableConversation,
} from './communication-fixture';

const clean: (() => void)[] = [];
afterEach(() => clean.splice(0).forEach((off) => off()));
async function mount(locale: 'en' | 'id' = 'en', failRead = false) {
  const f = await communicationFixture();
  const bootstrap = createBootstrap('localhost', '');
  if (bootstrap.status !== 'configured') throw Error('Fixture');
  if (failRead) {
    const original = f.server.custom!;
    f.server.custom = (path, init) =>
      path.includes('/communication-capabilities')
        ? Promise.resolve(denied(503))
        : original(path, init);
  }
  const locks = vi.fn();
  const view = render(
    <StrictMode>
      <DraftContext value={new DraftRegistry()}>
        <RoutingContext
          value={{
            site: bootstrap.site,
            locale,
            access: { kind: 'staff', roles: ['cs'] },
            switchLanguage: () => {},
          }}
        >
          <StaffCommunication
            queue={f.queue}
            caseId={caseId(4)}
            onLockChange={locks}
          />
        </RoutingContext>
      </DraftContext>
    </StrictMode>,
  );
  clean.push(() => {
    view.unmount();
    f.dispose();
  });
  return { ...f, ...view, locks };
}

it('mounted English pane sends once through explicit controls and reports queued rather than delivered', async () => {
  const f = await mount();
  await waitFor(() =>
    expect(screen.getByLabelText('Original reply')).toBeEnabled(),
  );
  expect(f.state.sends).toBe(0);
  fireEvent.change(screen.getByLabelText('Original reply'), {
    target: { value: 'Please wait for our email.' },
  });
  await waitFor(() => expect(f.locks).toHaveBeenLastCalledWith(true));
  fireEvent.click(screen.getByRole('button', { name: 'Send email reply' }));
  await screen.findByText(/Reply saved and queued for email/);
  expect(f.state.sends).toBe(1);
  expect(screen.queryByText(/^Delivered$/)).not.toBeInTheDocument();
  expect(screen.getByLabelText('Original reply')).toHaveValue('');
});

it('Indonesian UI preserves original text and requires human review before sending a translation', async () => {
  const f = await mount('id');
  await waitFor(() =>
    expect(screen.getByLabelText('Balasan asli')).toBeEnabled(),
  );
  fireEvent.change(screen.getByLabelText('Bahasa teks asli'), {
    target: { value: 'id' },
  });
  fireEvent.change(screen.getByLabelText('Balasan asli'), {
    target: { value: 'Mohon tunggu.' },
  });
  fireEvent.click(
    screen.getByRole('button', { name: 'Pratinjau terjemahan Inggris' }),
  );
  const reviewed = await screen.findByLabelText('Terjemahan yang ditinjau');
  expect(screen.getByLabelText('Balasan asli')).toHaveValue('Mohon tunggu.');
  expect(f.state.prepares).toBe(1);
  expect(f.state.approvals).toBe(0);
  fireEvent.change(reviewed, {
    target: { value: 'We will check and email you.' },
  });
  fireEvent.click(
    screen.getByRole('button', { name: 'Setujui dan kirim terjemahan' }),
  );
  await screen.findByText(/Balasan disimpan dan masuk antrean email/);
  expect(f.state.approvals).toBe(1);
  expect(f.state.draft?.reviewed_body).toBe('We will check and email you.');
});

it('lost reply response offers GET recovery and does not repeat a send', async () => {
  const f = await mount();
  f.state.lostSend = true;
  await waitFor(() =>
    expect(screen.getByLabelText('Original reply')).toBeEnabled(),
  );
  fireEvent.change(screen.getByLabelText('Original reply'), {
    target: { value: 'Please check this request.' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Send email reply' }));
  const check = await screen.findByRole('button', {
    name: 'Check saved result',
  });
  expect(f.state.sends).toBe(1);
  fireEvent.click(check);
  await screen.findByText(/Reply saved and queued for email/);
  expect(f.state.sends).toBe(1);
});

it('a read error remains explicit and never starts an automatic retry loop', async () => {
  const f = await mount('en', true);
  await screen.findByRole('alert');
  const before = f.server.requests.filter((r) =>
    r.path.includes('/communication-capabilities'),
  ).length;
  await new Promise((resolve) => setTimeout(resolve, 30));
  expect(
    f.server.requests.filter((r) =>
      r.path.includes('/communication-capabilities'),
    ),
  ).toHaveLength(before);
  expect(f.server.requests.every((r) => r.method === 'GET')).toBe(true);
});

it('unavailable email still permits switching to human chat or discarding locally', async () => {
  const f = await mount();
  let chat!: Awaited<ReturnType<typeof enableConversation>>;
  await act(async () => {
    chat = await enableConversation(f);
  });
  fireEvent.click(
    await screen.findByRole('button', { name: 'Accept conversation' }),
  );
  await waitFor(() =>
    expect(screen.getByLabelText('Original reply')).toBeEnabled(),
  );
  fireEvent.change(screen.getByLabelText('Reply channel'), {
    target: { value: 'email' },
  });
  fireEvent.change(screen.getByLabelText('Original reply'), {
    target: { value: 'Reply draft to review.' },
  });
  const original = f.server.custom!,
    before = f.server.requests.length;
  f.server.custom = (path, init) =>
    path.includes('/communication-capabilities')
      ? Promise.resolve(
          f.json({
            case_id: caseId(4),
            case_version: chat.chat.case_version,
            membership_id: MEMBER,
            email_available: false,
          }),
        )
      : original(path, init);
  fireEvent.click(screen.getByRole('button', { name: 'Refresh messages' }));
  await waitFor(() =>
    expect(
      screen.getByRole('button', { name: 'Send email reply' }),
    ).toBeDisabled(),
  );
  expect(screen.getByLabelText('Reply channel')).toBeEnabled();
  expect(
    screen.getByRole('button', { name: 'Discard reply draft' }),
  ).toBeEnabled();
  fireEvent.change(screen.getByLabelText('Reply channel'), {
    target: { value: 'chat' },
  });
  expect(screen.getByRole('button', { name: 'Send chat reply' })).toBeEnabled();
  expect(f.server.requests.slice(before).every((r) => r.method === 'GET')).toBe(
    true,
  );
  expect(chat.replies).toBe(0);
});

it('actual workspace recovers a postcommit503 through Refresh cases without resetting staff auth', async () => {
  const f = await communicationFixture(),
    bootstrap = createBootstrap('localhost', '');
  if (bootstrap.status !== 'configured') throw Error('Fixture');
  const original = f.server.custom!;
  f.server.custom = (path, init) => {
    const result = original(path, init);
    if (!path.endsWith('/email-messages')) return result;
    return Promise.resolve(result).then(() =>
      f.json(
        {
          error: {
            code: 'SOURCE_UNAVAILABLE',
            message: 'Synthetic postcommit error',
            request_id: '11111111-1111-4111-8111-111111111111',
            retryable: true,
          },
        },
        503,
      ),
    );
  };
  const router = createMemoryRouter(
    createAppRoutes(bootstrap.site, undefined, [], f.auth),
    { initialEntries: [`/staff/cases/${caseId(4)}?lang=en`] },
  );
  const view = render(<App bootstrap={bootstrap} router={router} />);
  clean.push(() => {
    view.unmount();
    router.dispose();
    f.dispose();
  });
  await waitFor(() =>
    expect(screen.getByLabelText('Original reply')).toBeEnabled(),
  );
  const epoch = f.auth.getState().epoch;
  fireEvent.change(screen.getByLabelText('Original reply'), {
    target: { value: 'Private reply with a lost result.' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Send email reply' }));
  await screen.findByRole('heading', { name: 'Case unavailable' });
  fireEvent.click(screen.getByRole('button', { name: 'Refresh cases' }));
  const check = await screen.findByRole('button', {
    name: 'Check saved result',
  });
  expect(f.auth.getState().epoch).toBe(epoch);
  expect(f.state.sends).toBe(1);
  fireEvent.click(check);
  await screen.findByText(/Reply saved and queued for email/);
  expect(f.state.sends).toBe(1);
  expect(f.auth.getState().epoch).toBe(epoch);
});

it.each(['queue', 'detail'] as const)(
  'persistent SOURCE failure at %s stays bounded until explicit workspace refresh',
  async (stage) => {
    const f = await communicationFixture(),
      bootstrap = createBootstrap('localhost', '');
    if (bootstrap.status !== 'configured') throw Error('Fixture');
    let reads = 0;
    const original = f.server.custom!;
    f.server.custom = (path, init) => {
      const target =
        stage === 'queue'
          ? path.includes('/staff/queues/')
          : path.includes(`/staff/cases/${caseId(4)}`);
      if (!target) return original(path, init);
      reads++;
      // A defective retry loop is stopped deterministically after four reads,
      // rather than flooding the test worker while this regression fails.
      return Promise.resolve(
        reads > 3
          ? denied(503)
          : f.json(
              {
                error: {
                  code: 'SOURCE_UNAVAILABLE',
                  message: 'Persistent synthetic outage',
                  request_id: '11111111-1111-4111-8111-111111111111',
                  retryable: true,
                },
              },
              503,
            ),
      );
    };
    const router = createMemoryRouter(
      createAppRoutes(bootstrap.site, undefined, [], f.auth),
      { initialEntries: [`/staff/cases/${caseId(4)}?lang=en`] },
    );
    const view = render(<App bootstrap={bootstrap} router={router} />);
    clean.push(() => {
      view.unmount();
      router.dispose();
      f.dispose();
    });
    await waitFor(() => expect(reads).toBeGreaterThan(0));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 40));
    });
    const before = reads;
    expect(before).toBeLessThanOrEqual(stage === 'queue' ? 1 : 2);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 40));
    });
    expect(reads).toBe(before);
    fireEvent.click(screen.getByRole('button', { name: 'Refresh cases' }));
    await waitFor(() => expect(reads).toBeGreaterThan(before));
  },
);
