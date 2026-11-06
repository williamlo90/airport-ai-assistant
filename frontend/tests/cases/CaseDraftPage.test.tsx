import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { StrictMode } from 'react';
import { createMemoryRouter } from 'react-router';
import { afterAll, afterEach, beforeAll, expect, it } from 'vitest';
import { caseFixture, DRAFT, error } from './fixture';
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
      this.dispatchEvent(new Event('close'));
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
async function mount(
  options: {
    intake?: boolean;
    setup?: (f: ReturnType<typeof caseFixture>) => void;
  } = {},
) {
  const f = caseFixture();
  owned.push(f);
  await f.ready();
  options.setup?.(f);
  const bootstrap = createBootstrap('localhost', '');
  if (bootstrap.status !== 'configured') throw Error('fixture');
  const router = createMemoryRouter(
    createAppRoutes(
      bootstrap.site,
      undefined,
      [
        {
          path: 'test-review/:draftId',
          element: (
            <CaseDraftContext value={f.cases}>
              <SubmissionContext value={null}>
                <CaseDraftPage intake={options.intake ?? false} />
              </SubmissionContext>
            </CaseDraftContext>
          ),
        },
      ],
      f.auth,
    ),
    { initialEntries: ['/test-review/' + DRAFT + '?lang=en'] },
  );
  render(
    <StrictMode>
      <App bootstrap={bootstrap} router={router} />
    </StrictMode>,
  );
  await screen.findByRole('heading', { name: 'Review your support request' });
  await waitFor(() => expect(f.cases.getState().phase).not.toBe('loading'));
  return { ...f, router };
}
it('loads deep preview without POST and shows exact server destination, language and summary', async () => {
  const f = await mount();
  expect(f.previewServer.creates).toBe(0);
  expect(await screen.findByText('Westbridge Airport')).toBeInTheDocument();
  expect(
    screen.getByText(/Westbridge AirportCustomer support/),
  ).toBeInTheDocument();
  expect(
    screen.getByText('Server preview: parking support request.'),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: /send request|confirm/i }),
  ).not.toBeInTheDocument();
});
it('edits the whole report and replaces preview after saved response, with per-section editor focus', async () => {
  const f = await mount();
  fireEvent.click(await screen.findByRole('button', { name: 'Edit email' }));
  const dialog = await screen.findByRole('dialog');
  expect(within(dialog).getByLabelText('Email for follow-up')).toHaveFocus();
  fireEvent.change(within(dialog).getByLabelText('Short summary'), {
    target: { value: 'Corrected flight delay report' },
  });
  fireEvent.change(within(dialog).getByLabelText('Email for follow-up'), {
    target: { value: 'updated@example.com' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
  expect(
    await screen.findByText('Corrected flight delay report'),
  ).toBeInTheDocument();
  expect(screen.getByText('updated@example.com')).toBeInTheDocument();
  expect(f.previewServer.patches).toBe(1);
  expect(f.previewServer.bodies[0]).toMatchObject({
    expected_version: 1,
    payload: {
      summary: 'Corrected flight delay report',
      contact_email: 'updated@example.com',
      category: 'booking_issue',
      service_type: 'parking',
      attachment_ids: [],
    },
  });
});
it('cancel requires confirmation; keeping review sends nothing and confirmed DELETE renders cancelled', async () => {
  const f = await mount();
  fireEvent.click(await screen.findByRole('button', { name: 'Cancel draft' }));
  fireEvent.click(
    within(await screen.findByRole('dialog')).getByRole('button', {
      name: 'Keep reviewing',
    }),
  );
  expect(f.previewServer.cancels).toBe(0);
  fireEvent.click(screen.getByRole('button', { name: 'Cancel draft' }));
  fireEvent.click(
    within(await screen.findByRole('dialog')).getByRole('button', {
      name: 'Yes, cancel draft',
    }),
  );
  expect(
    await screen.findByText('This preview was cancelled.'),
  ).toBeInTheDocument();
  expect(f.previewServer.cancels).toBe(1);
  expect(
    screen.queryByRole('button', { name: 'Cancel draft' }),
  ).not.toBeInTheDocument();
});
it('invalid form uses a focused summary and linked inline errors without an API request', async () => {
  const f = await mount({ intake: true });
  fireEvent.change(screen.getByLabelText('Short summary'), {
    target: { value: '' },
  });
  fireEvent.change(screen.getByLabelText('Email for follow-up'), {
    target: { value: 'invalid email' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Prepare review' }));
  const alert = await screen.findByRole('alert');
  expect(alert).toHaveFocus();
  expect(
    within(alert).getByRole('link', { name: 'Email for follow-up' }),
  ).toBeInTheDocument();
  expect(f.previewServer.creates).toBe(0);
});
it('generic422 is form-level and keeps the editor; no fabricated contact-field API error', async () => {
  const f = await mount();
  f.previewServer.handler = (m) =>
    m === 'PATCH' ? Promise.resolve(error(422, 'VALIDATION_ERROR')) : null;
  fireEvent.click(await screen.findByRole('button', { name: 'Edit report' }));
  fireEvent.change(
    within(await screen.findByRole('dialog')).getByLabelText('Short summary'),
    { target: { value: 'Keep local corrected text' } },
  );
  fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
  expect(
    await screen.findByText(
      'Check the format and completeness of your request.',
    ),
  ).toBeInTheDocument();
  expect(screen.getByLabelText('Short summary')).toHaveValue(
    'Keep local corrected text',
  );
  expect(f.previewServer.patches).toBe(0);
});
it('expired GET is read-only and displayed without claiming a request was sent', async () => {
  const f = await mount({
    setup: (f) => {
      f.previewServer.draft.state = 'expired';
    },
  });
  expect(
    await screen.findByText(
      'This preview has expired. Its original deadline cannot be extended.',
    ),
  ).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Edit report' })).toBeDisabled();
  expect(f.previewServer.creates).toBe(0);
});
it('foreign or revoked deep preview is cleared and never appears behind an error message', async () => {
  await mount({
    setup: (f) => {
      f.previewServer.handler = (m) =>
        m === 'GET' ? Promise.resolve(error(404, 'RESOURCE_NOT_FOUND')) : null;
    },
  });
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'This information is not available.',
  );
  expect(
    screen.queryByText('Parking extension question'),
  ).not.toBeInTheDocument();
});
it('committed view reports only the genuine result reference, not a pending request or invented receipt', async () => {
  await mount({
    setup: (f) => {
      f.previewServer.draft.state = 'committed';
      f.previewServer.draft.result_ref = 'CASE-' + 'a'.repeat(32);
    },
  });
  expect(await screen.findByText('Result recorded')).toBeInTheDocument();
  expect(screen.queryByText('Request not sent')).not.toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'Cancel draft' }),
  ).not.toBeInTheDocument();
});
it('switching language keeps the authorized preview and does not modify the server language', async () => {
  const f = await mount();
  fireEvent.click(
    await screen.findByRole('button', { name: 'Bahasa Indonesia' }),
  );
  expect(
    await screen.findByRole('heading', { name: 'Tinjau permintaan bantuan' }),
  ).toBeInTheDocument();
  expect(f.previewServer.patches).toBe(0);
  expect(f.cases.getState().draft?.language).toBe('en');
});
it('auth recheck clears a visible editor until a fresh resource GET succeeds', async () => {
  const f = await mount();
  fireEvent.click(await screen.findByRole('button', { name: 'Edit report' }));
  fireEvent.change(
    within(await screen.findByRole('dialog')).getByLabelText('Short summary'),
    { target: { value: 'Scoped local change' } },
  );
  await act(() => f.auth.recheck());
  await waitFor(() => expect(f.cases.getState().phase).toBe('review'));
  expect(screen.getByLabelText('Short summary')).toHaveValue(
    'Scoped local change',
  );
});
