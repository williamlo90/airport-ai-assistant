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
import { submissionFixture, DRAFT, ID, OTHER } from './submission-fixture';
import { App } from '../../src/app/App';
import { createBootstrap } from '../../src/app/config/publicSite';
import { createAppRoutes } from '../../src/app/routing/routes';
import { CaseDraftContext } from '../../src/features/cases/context';
import { SubmissionContext } from '../../src/features/cases/submission';
import { CaseDraftPage } from '../../src/features/cases/CaseDraftPage';
import { ChatPage } from '../../src/features/chat/ChatPage';
import { ChatContext } from '../../src/features/chat/context';

const owned: ReturnType<typeof submissionFixture>[] = [];
afterEach(() => owned.splice(0).forEach((f) => f.dispose()));
async function mount(
  options: {
    intake?: boolean;
    locale?: 'en' | 'id';
    reportOnly?: boolean;
    chat?: boolean;
    linkedCase?: boolean;
  } = {},
) {
  const f = submissionFixture();
  owned.push(f);
  f.server.kind = options.linkedCase ? 'customer' : 'anonymous';
  f.server.principal = options.linkedCase ? OTHER : null;
  f.previewServer.draft = {
    ...f.previewServer.draft,
    action_type: options.reportOnly ? 'create_case' : 'request_handoff',
    case_id: options.linkedCase ? OTHER : null,
    payload: {
      category: 'information',
      service_type: 'general',
      summary: 'Waiting area question',
      customer_report: 'My original waiting area question.',
      contact_email: null,
      booking_reference: null,
      transaction_reference: null,
      attachment_ids: [],
    },
  };
  await f.auth.start();
  if (options.chat) {
    f.server.meta.last_sequence = 1;
    f.server.events = [
      {
        id: ID,
        sequence: 1,
        created_at: '2026-11-05T12:00:00Z',
        event_type: 'message_received',
        payload: {
          message_id: ID,
          author_type: 'customer',
          language: 'en',
          body: 'My original waiting area question.',
        },
      },
    ];
  }
  await f.store.open(ID);
  if (options.intake) {
    f.store.input({ body: 'My original waiting area question.', service: '' });
    await f.cases.prepareFromChat('information', '', 'request_handoff');
  }
  const bootstrap = createBootstrap('localhost', '');
  if (bootstrap.status !== 'configured') throw Error('fixture');
  const element = (
    <ChatContext value={f.store}>
      <CaseDraftContext value={f.cases}>
        <SubmissionContext value={f.submission}>
          {options.chat ? (
            <ChatPage />
          ) : (
            <CaseDraftPage intake={options.intake ?? false} />
          )}
        </SubmissionContext>
      </CaseDraftContext>
    </ChatContext>
  );
  const router = createMemoryRouter(
    createAppRoutes(
      bootstrap.site,
      undefined,
      [
        {
          path: options.chat
            ? 'test-handoff/:conversationId'
            : 'test-handoff/:draftId',
          element,
        },
      ],
      f.auth,
    ),
    {
      initialEntries: [
        '/test-handoff/' +
          (options.chat ? ID : DRAFT) +
          '?lang=' +
          (options.locale ?? 'en'),
      ],
    },
  );
  render(
    <StrictMode>
      <App bootstrap={bootstrap} router={router} />
    </StrictMode>,
  );
  return { ...f, router };
}
it.each(['en', 'id'] as const)(
  'linked-case %s review preserves contact settings without offering email changes',
  async (locale) => {
    const f = await mount({ linkedCase: true, locale });
    expect(
      await screen.findByText(
        locale === 'en'
          ? 'Follow-up uses the contact settings already recorded on your existing case.'
          : 'Tindak lanjut memakai pengaturan kontak yang sudah tercatat pada kasus Anda.',
      ),
    ).toBeVisible();
    expect(
      screen.queryByRole('button', { name: 'Edit email' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(
        locale === 'en'
          ? 'No follow-up address'
          : 'Alamat tindak lanjut belum diisi',
      ),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole('link', { name: 'CASE-' + OTHER.replaceAll('-', '') }),
    ).toHaveAttribute('href', '/help/cases/' + OTHER + '?lang=' + locale);
    expect(f.submits.requests).toHaveLength(0);
  },
);

it('anonymous general review allows explicit staff request without email and does not submit on mount', async () => {
  const f = await mount();
  const button = await screen.findByRole('button', {
    name: 'Request staff assistance',
  });
  expect(button).toBeEnabled();
  expect(f.submits.commits).toBe(0);
  expect(screen.getByText('My original waiting area question.')).toBeVisible();
  expect(
    screen.queryByText('Verify email', { exact: true }),
  ).not.toBeInTheDocument();
  fireEvent.click(button);
  const link = await screen.findByRole('link', {
    name: 'Continue conversation and check staff updates',
  });
  expect(link).toHaveAttribute(
    'href',
    '/help/conversations/' + ID + '?lang=en',
  );
  expect(f.submits.commits).toBe(1);
  expect(
    screen.queryByText('Waiting for a staff member', { exact: true }),
  ).not.toBeInTheDocument();
});
it('Indonesian handoff editor keeps original story and restricts anonymous contact/reference fields', async () => {
  const f = await mount({ intake: true, locale: 'id' });
  const story = await screen.findByRole('textbox', { name: 'Laporan Anda' });
  expect(story).toHaveValue('My original waiting area question.');
  expect(screen.getByRole('combobox', { name: 'Layanan' })).toBeDisabled();
  expect(
    screen.getByRole('textbox', {
      name: 'Email tindak lanjut (opsional, alamat terverifikasi)',
    }),
  ).toHaveAttribute('readonly');
  expect(
    screen.getByRole('textbox', { name: 'Referensi booking (opsional)' }),
  ).toHaveAttribute('readonly');
  fireEvent.change(screen.getByRole('textbox', { name: 'Ringkasan singkat' }), {
    target: { value: 'Tinjau pertanyaan area tunggu' },
  });
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Siapkan tinjauan' }));
  });
  await waitFor(() => expect(f.previewServer.creates).toBe(1));
  expect(f.previewServer.bodies[0]).toMatchObject({
    action_type: 'request_handoff',
    payload: { contact_email: null },
  });
  expect(f.submits.commits).toBe(0);
});
it('anonymous report-only create_case keeps its original authentication restriction', async () => {
  const f = await mount({ reportOnly: true });
  const button = await screen.findByRole('button', {
    name: 'Send support request',
  });
  expect(button).toBeDisabled();
  fireEvent.click(button);
  expect(f.submits.requests).toHaveLength(0);
});
it('chat staff action preserves durable customer story without a model run or automatic draft/confirm', async () => {
  const f = await mount({ chat: true });
  const button = await screen.findByRole('button', {
    name: 'Review staff request',
  });
  expect(f.previewServer.creates).toBe(0);
  fireEvent.click(button);
  await waitFor(() =>
    expect(f.router.state.location.pathname).toBe('/help/request'),
  );
  expect(f.cases.getState()).toMatchObject({
    actionType: 'request_handoff',
    form: {
      customer_report: 'My original waiting area question.',
      contact_email: '',
    },
  });
  expect(f.previewServer.creates).toBe(0);
  expect(f.submits.commits).toBe(0);
  expect(
    f.fetcher.mock.calls.some(([path]) => String(path).includes('/agent-runs')),
  ).toBe(false);
});
