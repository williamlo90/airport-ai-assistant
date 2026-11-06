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
import { submissionFixture, DRAFT, result } from './submission-fixture';
import { error } from './fixture';
import { App } from '../../src/app/App';
import { createBootstrap } from '../../src/app/config/publicSite';
import { createAppRoutes } from '../../src/app/routing/routes';
import { CaseDraftContext } from '../../src/features/cases/context';
import { SubmissionContext } from '../../src/features/cases/submission';
import { CaseDraftPage } from '../../src/features/cases/CaseDraftPage';
const owned: ReturnType<typeof submissionFixture>[] = [];
afterEach(() => owned.splice(0).forEach((f) => f.dispose()));
async function mount(
  setup?: (f: ReturnType<typeof submissionFixture>) => void,
  missing = false,
) {
  const f = submissionFixture();
  owned.push(f);
  await f.ready();
  setup?.(f);
  const bootstrap = createBootstrap('localhost', '');
  if (bootstrap.status !== 'configured') throw Error('fixture');
  const element = (
    <CaseDraftContext value={f.cases}>
      <SubmissionContext value={f.submission}>
        <CaseDraftPage />
      </SubmissionContext>
    </CaseDraftContext>
  );
  const router = createMemoryRouter(
    createAppRoutes(
      bootstrap.site,
      undefined,
      [
        { path: 'test-submit/:draftId', element },
        { path: 'test-submit', element },
      ],
      f.auth,
    ),
    {
      initialEntries: [
        (missing ? '/test-submit' : '/test-submit/' + DRAFT) + '?lang=en',
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
it('requires explicit send and shows real reference without email-delivery or invented case updates', async () => {
  const f = await mount();
  fireEvent.click(
    await screen.findByRole('button', { name: 'Send support request' }),
  );
  expect(
    await screen.findByRole('heading', {
      name: 'Your support request is recorded',
    }),
  ).toHaveFocus();
  expect(screen.getByText(result().result_ref)).toBeInTheDocument();
  expect(
    screen.getByText(/does not confirm that an email/),
  ).toBeInTheDocument();
  expect(screen.queryByText('customer@example.com')).not.toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'Cancel draft' }),
  ).not.toBeInTheDocument();
  expect(f.submits.requests).toHaveLength(1);
  expect(f.submits.commits).toBe(1);
  fireEvent.click(
    screen.getByRole('button', { name: 'Check recorded result' }),
  );
  await screen.findByRole('heading', {
    name: 'Your support request is recorded',
  });
  expect(f.submits.requests).toHaveLength(1);
});
it('lost response resolves to receipt with GET and no automatic resend', async () => {
  const f = await mount((f) => {
    f.submits.loseResponse = true;
  });
  fireEvent.click(
    await screen.findByRole('button', { name: 'Send support request' }),
  );
  await screen.findByRole('heading', {
    name: 'Your support request is recorded',
  });
  expect(f.submits.requests).toHaveLength(1);
});
it('unknown pending locks edits and ordinary send, preserves key across language switch and explicit retry', async () => {
  const f = await mount((f) => {
    f.submits.loseBeforeCommit = true;
  });
  fireEvent.click(
    await screen.findByRole('button', { name: 'Send support request' }),
  );
  await screen.findByRole('button', { name: 'Retry the same request' });
  expect(screen.getByRole('button', { name: 'Edit report' })).toBeDisabled();
  expect(
    screen.queryByRole('button', { name: 'Send support request' }),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Bahasa Indonesia' }));
  await screen.findByRole('button', {
    name: 'Coba ulang permintaan yang sama',
  });
  expect(f.submits.requests).toHaveLength(1);
  f.submits.loseBeforeCommit = false;
  fireEvent.click(
    screen.getByRole('button', { name: 'Coba ulang permintaan yang sama' }),
  );
  await screen.findByRole('heading', {
    name: 'Permintaan bantuan Anda tercatat',
  });
  expect(f.submits.requests[0]).toEqual(f.submits.requests[1]);
  expect(f.submits.commits).toBe(1);
});
it('recovered receipt renders without preview access and ignores the old pending deadline', async () => {
  const f = await mount((f) => {
    f.previewServer.draft.state = 'committed';
    f.previewServer.draft.expires_at = '2001-01-01T00:00:00Z';
    f.previewServer.handler = () =>
      Promise.resolve(error(404, 'RESOURCE_NOT_FOUND'));
  });
  await screen.findByRole('heading', {
    name: 'Your support request is recorded',
  });
  expect(screen.getByText(result().result_ref)).toBeInTheDocument();
  expect(screen.queryByText(/preview has expired/i)).not.toBeInTheDocument();
  expect(f.submits.requests).toHaveLength(0);
});
it('missing pointer offers guidance without lookup, creation or a replacement submission', async () => {
  const f = await mount(undefined, true);
  await screen.findByText(/original request link is missing/);
  expect(
    screen.queryByRole('button', { name: 'Send support request' }),
  ).not.toBeInTheDocument();
  expect(f.submits.requests).toHaveLength(0);
  expect(f.previewServer.creates).toBe(0);
});
it('generic denied status hides old private preview and never offers send', async () => {
  const f = await mount((f) => {
    f.submits.handler = () => Promise.resolve(error(404, 'RESOURCE_NOT_FOUND'));
  });
  await screen.findByRole('button', { name: 'Check request status' });
  expect(screen.queryByText('customer@example.com')).not.toBeInTheDocument();
  expect(
    screen.queryByText(/Parking extension question/),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'Send support request' }),
  ).not.toBeInTheDocument();
  expect(f.submits.requests).toHaveLength(0);
});
it('422 exposes generic correction and preserves edit controls without claiming contact verification', async () => {
  const f = await mount((f) => {
    f.submits.handler = (path) =>
      path.endsWith('/confirm')
        ? Promise.resolve(error(422, 'CASE_CONTACT_VERIFICATION_REQUIRED'))
        : null;
  });
  fireEvent.click(
    await screen.findByRole('button', { name: 'Send support request' }),
  );
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Edit email' })).toBeEnabled(),
  );
  expect(
    screen.getByText('Check the format and completeness of your request.'),
  ).toBeInTheDocument();
  expect(f.submission.getState().receipt).toBeNull();
});
it('a pending reload never replays consent or POSTs when the route is remounted', async () => {
  const f = await mount();
  await screen.findByRole('button', { name: 'Send support request' });
  await act(() => f.router.navigate('/test-submit?lang=en'));
  await screen.findByText(/original request link is missing/);
  await act(() => f.router.navigate('/test-submit/' + DRAFT + '?lang=en'));
  await screen.findByRole('button', { name: 'Send support request' });
  expect(f.submits.requests).toHaveLength(0);
});
