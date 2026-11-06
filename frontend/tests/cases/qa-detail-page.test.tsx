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
import { detailFixture, CASE, closedCase } from './detail-fixture';
import { App } from '../../src/app/App';
import { createBootstrap } from '../../src/app/config/publicSite';
import { createAppRoutes } from '../../src/app/routing/routes';
import { CaseDetailContext } from '../../src/features/cases/detail-store';
import { CaseDetailPage } from '../../src/features/cases/CaseDetailPage';

const owned: ReturnType<typeof detailFixture>[] = [];
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

async function mount(active = false) {
  const f = detailFixture();
  owned.push(f);
  await f.ready();
  if (active) {
    await f.details.continue(f.prepare());
    f.details.detach(CASE);
  }
  const bootstrap = createBootstrap('localhost', '');
  if (bootstrap.status !== 'configured') throw Error('fixture');
  const router = createMemoryRouter(
    createAppRoutes(
      bootstrap.site,
      undefined,
      [
        {
          path: 'qa-detail/:caseId',
          element: (
            <CaseDetailContext value={f.details}>
              <CaseDetailPage />
            </CaseDetailContext>
          ),
        },
        { path: 'qa-exit', element: <p>A different help flow</p> },
      ],
      f.auth,
    ),
    { initialEntries: ['/qa-detail/' + CASE + '?lang=en'] },
  );
  render(<App bootstrap={bootstrap} router={router} />);
  await screen.findByRole('heading', { name: 'Your case, at a glance' });
  await waitFor(() => expect(f.details.getState().phase).toBe('ready'));
  return { ...f, router };
}

it('unknown continuation requires Keep or Discard before route navigation and Keep preserves the exact retry command', async () => {
  const f = await mount();
  f.detailServer.loseBefore = true;
  fireEvent.click(
    screen.getByRole('button', { name: 'My issue is not resolved' }),
  );
  fireEvent.change(screen.getByLabelText('Your explanation'), {
    target: { value: 'The same issue remains unresolved.' },
  });
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(
    screen.getByRole('button', { name: 'Request further handling' }),
  );
  await waitFor(() => expect(f.details.getState().phase).toBe('unknown'));
  const original = f.detailServer.posts[0];
  await act(() => f.router.navigate('/qa-exit?lang=en'));
  expect(f.router.state.location.pathname).toBe('/qa-detail/' + CASE);
  const dialog = await screen.findByRole('dialog', {
    name: 'Leave unsaved changes?',
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Keep editing' }));
  f.detailServer.loseBefore = false;
  fireEvent.click(
    screen.getByRole('button', { name: 'Retry the same request' }),
  );
  await waitFor(() => expect(f.details.getState().notice).toBe('recorded'));
  expect(f.detailServer.posts).toHaveLength(2);
  expect(f.detailServer.posts[1]).toEqual(original);
});

it('reload reconstructs prior closure from server history while keeping observed active status separate from own success', async () => {
  const f = await mount(true);
  expect(
    screen.getByRole('heading', { name: 'Previous handling results' }),
  ).toBeInTheDocument();
  expect(screen.getAllByText(closedCase().resolution!)).not.toHaveLength(0);
  expect(
    screen.queryByText(
      'Your follow-up request was recorded. The current case status is shown above.',
    ),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'My issue is not resolved' }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole('textbox', { name: 'Your explanation' }),
  ).not.toBeInTheDocument();
  expect(
    screen.getByRole('textbox', { name: 'Your additional information' }),
  ).toHaveValue('');
  expect(f.detailServer.posts).toHaveLength(1);
});

it('soft auth checking retains an authorized explanation but requires renewed same-issue consent', async () => {
  const f = await mount();
  fireEvent.click(
    screen.getByRole('button', { name: 'My issue is not resolved' }),
  );
  fireEvent.change(screen.getByLabelText('Your explanation'), {
    target: { value: 'Retain only this plain explanation.' },
  });
  fireEvent.click(screen.getByRole('checkbox'));
  expect(screen.getByRole('checkbox')).toBeChecked();
  await act(() => f.auth.recheck());
  await waitFor(() =>
    expect(screen.getByLabelText('Your explanation')).toHaveValue(
      'Retain only this plain explanation.',
    ),
  );
  expect(screen.getByRole('checkbox')).not.toBeChecked();
  expect(f.detailServer.posts).toHaveLength(0);
});

it('discarding an unknown follow-up clears only local state and does not cancel an already recorded continuation', async () => {
  const f = await mount();
  f.detailServer.loseAfter = true;
  fireEvent.click(
    screen.getByRole('button', { name: 'My issue is not resolved' }),
  );
  fireEvent.change(screen.getByLabelText('Your explanation'), {
    target: { value: 'The same issue remains unresolved.' },
  });
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(
    screen.getByRole('button', { name: 'Request further handling' }),
  );
  await waitFor(() =>
    expect(f.details.getState()).toMatchObject({
      phase: 'unknown',
      detail: { status: 'new' },
    }),
  );
  await act(() => f.router.navigate('/qa-exit?lang=en'));
  const dialog = await screen.findByRole('dialog', {
    name: 'Leave unsaved changes?',
  });
  expect(dialog).toHaveTextContent('Requests already sent may still complete.');
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Discard changes and leave' }),
  );
  await waitFor(() =>
    expect(f.router.state.location.pathname).toBe('/qa-exit'),
  );
  expect(f.details.getState()).toMatchObject({
    phase: 'idle',
    detail: null,
    message: '',
  });
  expect(f.detailServer.applied).toBe(1);
  expect(f.detailServer.detail.status).toBe('new');
  expect(f.detailServer.posts).toHaveLength(1);
  expect(
    f.fetcher.mock.calls.filter(([, init]) => init?.method === 'DELETE'),
  ).toHaveLength(0);
});
