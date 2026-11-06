import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { createMemoryRouter } from 'react-router';
import { afterEach, expect, it } from 'vitest';
import { sourceFixture, TX } from './fixture';
import { App } from '../../src/app/App';
import { createBootstrap } from '../../src/app/config/publicSite';
import { createAppRoutes } from '../../src/app/routing/routes';
import { SourceContext } from '../../src/features/source/store';
import { SourcePage } from '../../src/features/source/SourcePage';

const owned: ReturnType<typeof sourceFixture>[] = [];
afterEach(() => owned.splice(0).forEach((f) => f.dispose()));
async function mount() {
  const f = sourceFixture();
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
          path: 'qa-source',
          element: (
            <SourceContext value={f.sources}>
              <SourcePage />
            </SourceContext>
          ),
        },
      ],
      f.auth,
    ),
    { initialEntries: ['/qa-source?lang=en'] },
  );
  render(<App bootstrap={bootstrap} router={router} />);
  await screen.findByRole('heading', { name: 'Check your booking or payment' });
  return { ...f, router };
}

it('invalid reference error stays associated with its input after following the focused error summary link', async () => {
  const f = await mount();
  fireEvent.change(screen.getByLabelText('Reference'), {
    target: { value: 'invalid / reference' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Check service record' }));
  const summary = await screen.findByRole('alert');
  expect(summary).toHaveFocus();
  fireEvent.click(within(summary).getByRole('link'));
  const input = screen.getByLabelText('Reference');
  await waitFor(() => expect(input).toHaveFocus());
  expect(input).toHaveAccessibleDescription(
    /1–80 letters, numbers, underscores or hyphens/,
  );
  expect(f.source.paths).toHaveLength(0);
});

it('unknown payment with authorized zero is displayed as unknown, not paid, while showing actual source money metadata', async () => {
  const f = await mount();
  f.source.transaction = {
    ...f.source.transaction,
    amount_minor: 0,
    status: 'unknown',
    refund_status: 'unknown',
  };
  fireEvent.change(screen.getByLabelText('Record type'), {
    target: { value: 'transaction' },
  });
  fireEvent.change(screen.getByLabelText('Reference'), {
    target: { value: TX },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Check service record' }));
  const article = await screen.findByRole('article', {
    name: 'Transaction ' + TX,
  });
  expect(
    within(article).getAllByText('Not confirmed by the source'),
  ).toHaveLength(2);
  expect(within(article).queryByText('Paid')).not.toBeInTheDocument();
  expect(article).toHaveTextContent('0.00');
  expect(article).toHaveTextContent('SGD');
  expect(f.source.paths).toEqual(['/api/v1/transactions/' + TX]);
});

it('renders booking schedule in its service zone and labels observation/update UTC timestamps separately', async () => {
  const f = await mount();
  fireEvent.change(screen.getByLabelText('Reference'), {
    target: { value: f.source.booking.reference },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Check service record' }));
  await screen.findByRole('region', { name: 'Booking details' });
  expect(screen.getByText('Starts').closest('div')).toHaveTextContent('09:00');
  expect(screen.getByText('Service timezone').closest('div')).toHaveTextContent(
    'Asia/Jakarta',
  );
  expect(screen.getByText('Observed at').closest('div')).toHaveTextContent(
    '02:00',
  );
  expect(screen.getByText('Observed at').closest('div')).toHaveTextContent(
    'UTC',
  );
  expect(
    screen.getByText('Source last updated').closest('div'),
  ).toHaveTextContent('01:00');
  expect(
    screen.getByText('Source last updated').closest('div'),
  ).toHaveTextContent('UTC');
  expect(f.router.state.location.search).toBe('?lang=en');
  await waitFor(() => expect(f.sources.getState().phase).toBe('ready'));
});
