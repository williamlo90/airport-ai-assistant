import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { createMemoryRouter } from 'react-router';
import { afterEach, expect, it } from 'vitest';
import { submissionFixture, DRAFT } from './submission-fixture';
import { error } from './fixture';
import { App } from '../../src/app/App';
import { createBootstrap } from '../../src/app/config/publicSite';
import { createAppRoutes } from '../../src/app/routing/routes';
import { CaseDraftContext } from '../../src/features/cases/context';
import { CaseDraftPage } from '../../src/features/cases/CaseDraftPage';
import { SubmissionContext } from '../../src/features/cases/submission';

const owned: ReturnType<typeof submissionFixture>[] = [];
afterEach(() => owned.splice(0).forEach((f) => f.dispose()));

it('unmounting submission fences a late conflict from the next intake route', async () => {
  const f = submissionFixture();
  owned.push(f);
  await f.ready();
  let finish!: (response: Response) => void;
  f.submits.handler = (path) =>
    path.endsWith('/confirm')
      ? new Promise((done) => {
          finish = done;
        })
      : null;
  const bootstrap = createBootstrap('localhost', '');
  if (bootstrap.status !== 'configured') throw Error('fixture');
  const wrapped = (intake: boolean) => (
    <SubmissionContext value={f.submission}>
      <CaseDraftContext value={f.cases}>
        <CaseDraftPage intake={intake} />
      </CaseDraftContext>
    </SubmissionContext>
  );
  const router = createMemoryRouter(
    createAppRoutes(
      bootstrap.site,
      undefined,
      [
        { path: 'qa-submit/:draftId', element: wrapped(false) },
        { path: 'qa-intake', element: wrapped(true) },
      ],
      f.auth,
    ),
    { initialEntries: ['/qa-submit/' + DRAFT + '?lang=en'] },
  );
  render(<App bootstrap={bootstrap} router={router} />);
  fireEvent.click(
    await screen.findByRole('button', { name: 'Send support request' }),
  );
  await screen.findByText(
    'Sending your support request. Please wait for its recorded result.',
  );
  await act(() => router.navigate('/qa-intake?lang=en'));
  await act(() => f.cases.prepareFromChat('contact', 'customer@example.com'));
  fireEvent.change(await screen.findByLabelText('Short summary'), {
    target: { value: 'New route intake text' },
  });
  await act(async () => {
    finish(error(409, 'VERSION_CONFLICT'));
  });
  await waitFor(() =>
    expect(f.cases.getState()).toMatchObject({
      phase: 'intake',
      draft: null,
      form: { summary: 'New route intake text' },
    }),
  );
  expect(screen.getByLabelText('Short summary')).toHaveValue(
    'New route intake text',
  );
});
