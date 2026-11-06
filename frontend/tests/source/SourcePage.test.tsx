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
import { afterEach, expect, it } from 'vitest';
import { sourceFixture, statusOnly, BOOK, TX, json, error } from './fixture';
import { App } from '../../src/app/App';
import { createBootstrap } from '../../src/app/config/publicSite';
import { createAppRoutes } from '../../src/app/routing/routes';
import { SourceContext } from '../../src/features/source/store';
import { SourcePage } from '../../src/features/source/SourcePage';
const owned: ReturnType<typeof sourceFixture>[] = [];
afterEach(() => owned.splice(0).forEach((f) => f.dispose()));
async function mount(setup?: (f: ReturnType<typeof sourceFixture>) => void) {
  const f = sourceFixture();
  owned.push(f);
  await f.ready();
  setup?.(f);
  const bootstrap = createBootstrap('localhost', '');
  if (bootstrap.status !== 'configured') throw Error('fixture');
  const router = createMemoryRouter(
    createAppRoutes(
      bootstrap.site,
      undefined,
      [
        {
          path: 'test-source',
          element: (
            <SourceContext value={f.sources}>
              <SourcePage />
            </SourceContext>
          ),
        },
      ],
      f.auth,
    ),
    { initialEntries: ['/test-source?lang=en'] },
  );
  render(
    <StrictMode>
      <App bootstrap={bootstrap} router={router} />
    </StrictMode>,
  );
  await screen.findByRole('heading', { name: 'Check your booking or payment' });
  return { ...f, router };
}
async function search(kind: 'booking' | 'transaction' = 'booking', ref = BOOK) {
  fireEvent.change(screen.getByLabelText('Record type'), {
    target: { value: kind },
  });
  fireEvent.change(screen.getByLabelText('Reference', { exact: true }), {
    target: { value: ref },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Check service record' }));
  await waitFor(() =>
    expect(
      screen.queryByText('Checking current access and source data…'),
    ).not.toBeInTheDocument(),
  );
}
it('has an explicit validated lookup without mount reads, private URL refs, or a case report prefill', async () => {
  const f = await mount();
  expect(f.source.paths).toHaveLength(0);
  fireEvent.click(screen.getByRole('button', { name: 'Check service record' }));
  const alert = await screen.findByRole('alert');
  expect(alert).toHaveFocus();
  expect(f.source.paths).toHaveLength(0);
  expect(f.router.state.location.search).toBe('?lang=en');
});
it('shows booking data, source timestamps and service timezone separately from customer reports', async () => {
  const f = await mount();
  await search();
  await screen.findByRole('region', { name: 'Booking details' });
  expect(screen.getByText('Asia/Jakarta')).toBeInTheDocument();
  expect(screen.getByText('Observed at')).toBeInTheDocument();
  expect(screen.getByText('Source last updated')).toBeInTheDocument();
  expect(
    screen.getByText(/separate from your report and case status/),
  ).toBeInTheDocument();
  expect(f.source.paths).toHaveLength(1);
});
it('displays authorized money then removes it completely on status-only downgrade', async () => {
  const f = await mount();
  await search('transaction', TX);
  await screen.findByText(/1,500\.50/);
  f.source.transaction = statusOnly();
  fireEvent.click(screen.getByRole('button', { name: 'Check service record' }));
  await screen.findByText('Not confirmed by the source');
  expect(screen.queryByText('Amount')).not.toBeInTheDocument();
  expect(screen.queryByText('Refund status')).not.toBeInTheDocument();
  expect(screen.queryByText(/1,500\.50/)).not.toBeInTheDocument();
  expect(
    screen.getByText(/Only payment status is available/),
  ).toBeInTheDocument();
});
it('failed source recheck hides private data and distinguishes unavailable from a failed payment', async () => {
  const f = await mount();
  await search('transaction', TX);
  await screen.findByText(/1,500\.50/);
  f.source.handler = () => Promise.resolve(error(503, 'SOURCE_UNAVAILABLE'));
  fireEvent.click(screen.getByRole('button', { name: 'Check service record' }));
  await screen.findByText(/This does not mean your booking or payment failed/);
  expect(screen.queryByText(/1,500\.50/)).not.toBeInTheDocument();
  expect(screen.queryByRole('article')).not.toBeInTheDocument();
});
it('status-only cannot leak even a null money field through a malformed backend projection', async () => {
  await mount((f) => {
    f.source.handler = () =>
      Promise.resolve(json({ ...statusOnly(), amount_minor: null }));
  });
  await search('transaction', TX);
  await screen.findByText(/could not be validated/);
  expect(screen.queryByText('Amount')).not.toBeInTheDocument();
  expect(screen.queryByRole('article')).not.toBeInTheDocument();
});
it('unknown with an explicitly known zero amount is distinct from absent details', async () => {
  await mount((f) => {
    f.source.transaction.status = 'unknown';
    f.source.transaction.amount_minor = 0;
  });
  await search('transaction', TX);
  await screen.findByText('Not confirmed by the source');
  expect(screen.getByText('Amount')).toBeInTheDocument();
  expect(screen.getByText(/0\.00/)).toBeInTheDocument();
  expect(screen.queryByText('Paid')).not.toBeInTheDocument();
});
it('empty authorized transaction page does not claim no payments exist and allows an opaque next page', async () => {
  await mount((f) => {
    f.source.page = { items: [], next_cursor: 'page.one' };
  });
  await search();
  fireEvent.click(
    await screen.findByRole('button', { name: 'Check related transactions' }),
  );
  await screen.findByText(
    'No transactions are available on this page for your account.',
  );
  expect(
    screen.getByRole('button', { name: 'Next transaction page' }),
  ).toBeInTheDocument();
  expect(screen.queryByText('Amount')).not.toBeInTheDocument();
  expect(screen.getByText(/not a total of all payments/)).toBeInTheDocument();
});
it('404 is generic and clears the reference and facts without claiming source ownership', async () => {
  const f = await mount((f) => {
    f.source.handler = () => Promise.resolve(error(404, 'RESOURCE_NOT_FOUND'));
  });
  await search();
  await screen.findByText(/not available to your account/);
  expect(screen.getByLabelText('Reference', { exact: true })).toHaveValue('');
  expect(
    screen.queryByRole('region', { name: 'Booking details' }),
  ).not.toBeInTheDocument();
  expect(f.source.paths).toHaveLength(1);
});
it('changing the reference immediately hides the previously read record without automatic fetching', async () => {
  const f = await mount();
  await search('transaction', TX);
  await screen.findByText(/1,500\.50/);
  fireEvent.change(screen.getByLabelText('Reference', { exact: true }), {
    target: { value: 'TX-OTHER' },
  });
  expect(screen.queryByRole('article')).not.toBeInTheDocument();
  expect(f.source.paths).toHaveLength(1);
});
it('auth recheck clears fields and changing locale never reads or sends a source fact into chat', async () => {
  const f = await mount();
  await search();
  await screen.findByRole('region', { name: 'Booking details' });
  fireEvent.click(screen.getByRole('button', { name: 'Bahasa Indonesia' }));
  await screen.findByRole('heading', {
    name: 'Periksa booking atau pembayaran Anda',
  });
  expect(f.source.paths).toHaveLength(1);
  await act(() => f.auth.recover());
  expect(screen.getByLabelText('Referensi', { exact: true })).toHaveValue('');
  expect(screen.queryByText('Terminal 2 Parking')).not.toBeInTheDocument();
  expect(f.store.getState().input.body).toBe('');
  expect(f.source.paths).toHaveLength(1);
});
it('related transactions are explicitly read with their own projection, not inferred from booking', async () => {
  await mount((f) => {
    f.source.page = { items: [statusOnly()], next_cursor: null };
  });
  await search();
  expect(screen.queryByRole('article')).not.toBeInTheDocument();
  fireEvent.click(
    await screen.findByRole('button', { name: 'Check related transactions' }),
  );
  const region = await screen.findByRole('region', {
    name: 'Related transactions',
  });
  expect(
    within(region).getByText('Not confirmed by the source'),
  ).toBeInTheDocument();
  expect(within(region).queryByText('Amount')).not.toBeInTheDocument();
});
