import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { createMemoryRouter } from 'react-router';
import { afterAll, afterEach, beforeAll, expect, it } from 'vitest';
import { caseFixture, DRAFT, NEXT } from './fixture';
import { App } from '../../src/app/App';
import { createBootstrap } from '../../src/app/config/publicSite';
import { createAppRoutes } from '../../src/app/routing/routes';
import { CaseDraftContext } from '../../src/features/cases/context';
import { CaseDraftPage } from '../../src/features/cases/CaseDraftPage';
import { SubmissionContext } from '../../src/features/cases/submission';

const owned: ReturnType<typeof caseFixture>[] = [];
const originalShow = Object.getOwnPropertyDescriptor(
  HTMLDialogElement.prototype,
  'showModal',
);
const originalClose = Object.getOwnPropertyDescriptor(
  HTMLDialogElement.prototype,
  'close',
);
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
    },
  });
});
afterAll(() => {
  if (originalShow)
    Object.defineProperty(
      HTMLDialogElement.prototype,
      'showModal',
      originalShow,
    );
  else Reflect.deleteProperty(HTMLDialogElement.prototype, 'showModal');
  if (originalClose)
    Object.defineProperty(HTMLDialogElement.prototype, 'close', originalClose);
  else Reflect.deleteProperty(HTMLDialogElement.prototype, 'close');
});
afterEach(() => owned.splice(0).forEach((f) => f.dispose()));

it('a cancel confirmation for one draft cannot cancel a newly selected draft', async () => {
  const f = caseFixture();
  owned.push(f);
  await f.ready();
  const bootstrap = createBootstrap('localhost', '');
  if (bootstrap.status !== 'configured') throw Error('fixture');
  const router = createMemoryRouter(
    createAppRoutes(
      bootstrap.site,
      undefined,
      [
        {
          path: 'qa-review/:draftId',
          element: (
            <SubmissionContext value={null}>
              <CaseDraftContext value={f.cases}>
                <CaseDraftPage />
              </CaseDraftContext>
            </SubmissionContext>
          ),
        },
      ],
      f.auth,
    ),
    { initialEntries: ['/qa-review/' + DRAFT + '?lang=en'] },
  );
  render(<App bootstrap={bootstrap} router={router} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Cancel draft' }));
  await screen.findByRole('dialog', { name: 'Cancel this draft?' });
  await act(() => router.navigate('/qa-review/' + NEXT + '?lang=en'));
  await waitFor(() => expect(f.cases.getState().draft?.draft_id).toBe(NEXT));
  const staleDialog = screen.queryByRole('dialog', {
    name: 'Cancel this draft?',
  });
  if (staleDialog) {
    fireEvent.click(
      within(staleDialog).getByRole('button', { name: 'Yes, cancel draft' }),
    );
    await waitFor(() => expect(f.cases.getState().phase).not.toBe('saving'));
  }
  expect(f.previewServer.cancels).toBe(0);
  expect(staleDialog).not.toBeInTheDocument();
});
