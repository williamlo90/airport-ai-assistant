import { act, render, screen, waitFor } from '@testing-library/react';
import { createMemoryRouter } from 'react-router';
import { afterEach, expect, it } from 'vitest';
import {
  submissionFixture,
  DRAFT,
  NEXT,
  ID,
  OTHER,
  json,
  result,
} from './submission-fixture';
import { error, preview } from './fixture';
import { SubmissionPage } from '../../src/features/cases/SubmissionPage';
import { App } from '../../src/app/App';
import { createBootstrap } from '../../src/app/config/publicSite';
import { createAppRoutes } from '../../src/app/routing/routes';

const owned: (() => void)[] = [];
afterEach(() => owned.splice(0).forEach((dispose) => dispose()));
it.each(['create_case', 'request_handoff'] as const)(
  'pending %s still rejects switching across the handoff boundary',
  async (action) => {
    const f = submissionFixture();
    owned.push(() => f.dispose());
    f.previewServer.draft.action_type = action;
    await f.ready();
    await expect(
      f.cases.prepareFromChat(
        'contact',
        'customer@example.com',
        action === 'create_case' ? 'request_handoff' : 'create_case',
      ),
    ).rejects.toMatchObject({ kind: 'invalid-request', outcome: 'not-sent' });
    expect(f.cases.getState().draft?.draft_id).toBe(DRAFT);
    expect(f.previewServer.creates).toBe(0);
    expect(f.submits.requests).toHaveLength(0);
  },
);
const committed = () => ({
  ...preview(),
  action_type: 'request_handoff' as const,
  state: 'committed' as const,
  result_ref: result().result_ref,
  expires_at: '2001-01-01T00:00:00Z',
});
const status = (id = DRAFT) => ({
  draft_id: id,
  state: 'committed',
  case_id: OTHER,
  result_ref: result().result_ref,
  version: 1,
  expires_at: null,
});
async function setup() {
  const f = submissionFixture();
  await f.auth.start();
  f.submits.handler = (path) =>
    path.startsWith('/api/v1/actions/')
      ? Promise.resolve(json(status(path.endsWith(NEXT) ? NEXT : DRAFT)))
      : null;
  owned.push(() => f.dispose());
  return f;
}
function mount(f: Awaited<ReturnType<typeof setup>>) {
  const bootstrap = createBootstrap('localhost', '');
  if (bootstrap.status !== 'configured') throw Error('fixture');
  const router = createMemoryRouter(
    createAppRoutes(
      bootstrap.site,
      undefined,
      [
        {
          path: 'receipt-a',
          element: (
            <SubmissionPage
              store={f.submission}
              id={DRAFT}
              review={() => <p>Review A</p>}
            />
          ),
        },
        {
          path: 'receipt-b',
          element: (
            <SubmissionPage
              store={f.submission}
              id={NEXT}
              review={() => <p>Review B</p>}
            />
          ),
        },
      ],
      f.auth,
    ),
    { initialEntries: ['/receipt-a?lang=en'] },
  );
  const view = render(<App bootstrap={bootstrap} router={router} />);
  owned.push(() => view.unmount());
  return { ...view, router };
}
it('receipt A never links to a retained draft B; only authorized exact-A context can provide its link', async () => {
  const f = await setup();
  f.previewServer.draft.conversation_id = OTHER;
  f.previewServer.draft.action_type = 'request_handoff';
  await f.cases.open(NEXT);
  let resolve!: (value: Response) => void;
  f.previewServer.handler = (method, path) =>
    method === 'GET' && path.endsWith(DRAFT)
      ? new Promise<Response>((r) => {
          resolve = r;
        })
      : null;
  mount(f);
  await screen.findByRole('heading', {
    name: 'Your support request is recorded',
  });
  expect(f.cases.getState().draft).toMatchObject({
    draft_id: NEXT,
    conversation_id: OTHER,
  });
  expect(
    screen.queryByRole('link', {
      name: 'Continue conversation and check staff updates',
    }),
  ).not.toBeInTheDocument();
  await act(async () => {
    resolve(json(committed()));
  });
  expect(
    await screen.findByRole('link', {
      name: 'Continue conversation and check staff updates',
    }),
  ).toHaveAttribute('href', '/help/conversations/' + ID + '?lang=en');
  expect(f.cases.getState().draft?.draft_id).toBe(NEXT);
});
it('cold committed handoff obtains an optional authorized link despite the old preview deadline', async () => {
  const f = await setup();
  f.previewServer.handler = () => Promise.resolve(json(committed()));
  mount(f);
  expect(
    await screen.findByRole('link', {
      name: 'Continue conversation and check staff updates',
    }),
  ).toHaveAttribute('href', '/help/conversations/' + ID + '?lang=en');
  expect(screen.getByText(result().result_ref)).toBeVisible();
  expect(f.cases.getState().draft).toBeNull();
  expect(f.submits.requests).toHaveLength(0);
});
it.each([403, 404])(
  'optional context denial %s preserves the independently authorized receipt',
  async (code) => {
    const f = await setup();
    f.previewServer.handler = () =>
      Promise.resolve(error(code, 'RESOURCE_NOT_FOUND'));
    mount(f);
    await screen.findByRole('heading', {
      name: 'Your support request is recorded',
    });
    await waitFor(() =>
      expect(
        f.fetcher.mock.calls.some(([path]) =>
          String(path).endsWith('/action-drafts/' + DRAFT),
        ),
      ).toBe(true),
    );
    expect(screen.getByText(result().result_ref)).toBeVisible();
    expect(f.submission.getState().phase).toBe('receipt');
    expect(
      screen.queryByRole('link', {
        name: 'Continue conversation and check staff updates',
      }),
    ).not.toBeInTheDocument();
  },
);
it('a mismatched preview cannot attach a different conversation to a valid receipt', async () => {
  const f = await setup();
  f.previewServer.handler = () =>
    Promise.resolve(
      json({ ...committed(), draft_id: NEXT, conversation_id: OTHER }),
    );
  await f.submission.open(DRAFT);
  await f.submission.loadReceiptContext(DRAFT);
  expect(f.submission.getState().phase).toBe('receipt');
  expect(f.submission.receiptConversation(DRAFT)).toBeNull();
});
it.each(['route', 'owner'] as const)(
  'late context after %s change cannot attach to a different receipt/session',
  async (change) => {
    const f = await setup();
    let resolve!: (value: Response) => void;
    f.previewServer.handler = () =>
      new Promise<Response>((r) => {
        resolve = r;
      });
    await f.submission.open(DRAFT);
    const read = f.submission.loadReceiptContext(DRAFT);
    if (change === 'route') {
      f.submission.detach(DRAFT);
      await f.submission.open(NEXT);
    } else {
      f.server.proof = 'q'.repeat(43);
      await f.auth.recheck();
    }
    resolve(json(committed()));
    await read;
    expect(f.submission.receiptConversation(DRAFT)).toBeNull();
    expect(f.submission.receiptConversation(NEXT)).toBeNull();
  },
);
