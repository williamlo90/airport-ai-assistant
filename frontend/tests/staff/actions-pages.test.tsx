import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { createMemoryRouter } from 'react-router';
import { afterEach, beforeEach, expect, it } from 'vitest';
import { App } from '../../src/app/App';
import { createBootstrap } from '../../src/app/config/publicSite';
import { createAppRoutes } from '../../src/app/routing/routes';
import { makeStaffFixture, caseId, MEMBER } from '../../src/dev/staff-fixtures';
const clean: (() => void)[] = [];
beforeEach(() => {
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', {
    configurable: true,
    value() {
      this.setAttribute('open', '');
    },
  });
  Object.defineProperty(HTMLDialogElement.prototype, 'close', {
    configurable: true,
    value() {
      this.removeAttribute('open');
    },
  });
});
afterEach(() => {
  clean.splice(0).forEach((f) => f());
  Reflect.deleteProperty(HTMLDialogElement.prototype, 'showModal');
  Reflect.deleteProperty(HTMLDialogElement.prototype, 'close');
});
async function mount(supervisor = false, n = 1) {
  const f = makeStaffFixture();
  if (supervisor) f.server.roles = ['supervisor'];
  await f.ready();
  const bootstrap = createBootstrap('localhost', '');
  if (bootstrap.status !== 'configured') throw Error('fixture');
  const router = createMemoryRouter(
    createAppRoutes(bootstrap.site, undefined, [], f.auth),
    { initialEntries: ['/staff/cases/' + caseId(n) + '?lang=en'] },
  );
  const view = render(<App bootstrap={bootstrap} router={router} />);
  clean.push(() => {
    view.unmount();
    router.dispose();
    f.dispose();
  });
  await screen.findByRole(
    'heading',
    { name: 'Customer report' },
    { timeout: 5000 },
  );
  return { ...f, router };
}
it('actual lazy workspace takes case and submits a real typed status command through controls', async () => {
  const f = await mount();
  fireEvent.click(screen.getByRole('button', { name: 'Take this case' }));
  await screen.findByLabelText('Handling status');
  fireEvent.change(screen.getByLabelText('Handling status'), {
    target: { value: 'awaiting_customer' },
  });
  fireEvent.change(screen.getByLabelText('Next action for this case'), {
    target: { value: 'Please send the parking confirmation.' },
  });
  expect(screen.getByRole('button', { name: 'Refresh cases' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Save status' }));
  await waitFor(() =>
    expect(f.server.requests.filter((v) => v.method === 'PATCH')).toHaveLength(
      2,
    ),
  );
  await waitFor(() =>
    expect(screen.getByLabelText('Next action for this case')).toHaveValue(''),
  );
  expect(
    screen.getAllByText('Please send the parking confirmation.')[0],
  ).toBeVisible();
  expect(
    screen.queryByRole('button', { name: 'Close case' }),
  ).not.toBeInTheDocument();
});
it('supervisor chooses server staff and confirmation cancellation sends no command', async () => {
  const f = await mount(true);
  fireEvent.click(screen.getByRole('button', { name: 'Find eligible staff' }));
  const select = await screen.findByLabelText('Eligible staff');
  fireEvent.change(select, { target: { value: MEMBER } });
  fireEvent.click(screen.getByRole('button', { name: 'Review assignment' }));
  const modal = screen.getByRole('dialog', {
    name: 'Confirm assignment change',
  });
  expect(within(modal).getByText(/Taylor Morgan/)).toBeVisible();
  fireEvent.click(
    within(modal).getByRole('button', { name: 'Keep current assignment' }),
  );
  expect(f.server.requests.some((v) => v.method === 'PATCH')).toBe(false);
  fireEvent.click(screen.getByRole('button', { name: 'Review assignment' }));
  fireEvent.click(screen.getByRole('button', { name: 'Confirm assignment' }));
  await screen.findByLabelText('Handling status');
  expect(f.server.requests.filter((v) => v.method === 'PATCH')).toHaveLength(1);
});
it('dirty next action blocks case navigation until explicit discard and clears prior case input', async () => {
  const f = await mount(false, 4);
  fireEvent.change(screen.getByLabelText('Next action for this case'), {
    target: { value: 'Private unsent draft.' },
  });
  fireEvent.click(screen.getByRole('link', { name: 'Back to cases' }));
  const dialog = await screen.findByRole('dialog', {
    name: 'Leave unsaved changes?',
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Keep editing' }));
  expect(screen.getByLabelText('Next action for this case')).toHaveValue(
    'Private unsent draft.',
  );
  fireEvent.click(screen.getByRole('link', { name: 'Back to cases' }));
  fireEvent.click(
    screen.getByRole('button', { name: 'Discard changes and leave' }),
  );
  await waitFor(() =>
    expect(f.router.state.location.pathname).toBe('/staff/cases'),
  );
  expect(screen.queryByText('Private unsent draft.')).not.toBeInTheDocument();
});

it('closure controls require evidence, show a cancellable outcome review, and remove staff edits after close', async () => {
  const f = await mount(false, 4);
  fireEvent.change(screen.getByLabelText('Closure reason'), {
    target: { value: 'no_response' },
  });
  fireEvent.change(screen.getByLabelText('Outcome for the customer'), {
    target: { value: 'We could not obtain the required confirmation.' },
  });
  fireEvent.change(screen.getByLabelText('Next step after closure'), {
    target: { value: 'Contact support if the same issue continues.' },
  });
  fireEvent.change(screen.getByLabelText('Follow-up evidence'), {
    target: { value: 'Followed up by email on 4 November, reference WB-42.' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Review closure' }));
  const modal = screen.getByRole('dialog', { name: 'Confirm case closure' });
  expect(
    within(modal).getByText('We could not obtain the required confirmation.'),
  ).toBeVisible();
  fireEvent.click(
    within(modal).getByRole('button', { name: 'Keep case open' }),
  );
  expect(f.server.requests.some((v) => v.method === 'PATCH')).toBe(false);
  fireEvent.click(screen.getByRole('button', { name: 'Review closure' }));
  fireEvent.click(screen.getByRole('button', { name: 'Close case' }));
  await waitFor(() =>
    expect(screen.queryByLabelText('Closure reason')).not.toBeInTheDocument(),
  );
  expect(f.server.requests.filter((v) => v.method === 'PATCH')).toHaveLength(1);
  expect(
    screen.getAllByText('We could not obtain the required confirmation.')[0],
  ).toBeVisible();
  expect(screen.queryByLabelText('Handling status')).not.toBeInTheDocument();
});
