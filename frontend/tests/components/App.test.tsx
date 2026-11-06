import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { StrictMode } from 'react';
import { describe, expect, it } from 'vitest';
import { createMemoryRouter } from 'react-router';
import { App } from '../../src/app/App';
import { createBootstrap } from '../../src/app/config/publicSite';
import { createAppRoutes } from '../../src/app/routing/routes';

function mount(entry = '/') {
  const bootstrap = createBootstrap('localhost', '');
  if (bootstrap.status !== 'configured') throw new Error('fixture');
  const router = createMemoryRouter(createAppRoutes(bootstrap.site), {
    initialEntries: [entry],
  });
  render(
    <StrictMode>
      <App bootstrap={bootstrap} router={router} />
    </StrictMode>,
  );
  return router;
}
describe('frontend bootstrap', () => {
  it('mounts help homepage with accessible identity and honest knowledge scope', () => {
    mount();
    expect(
      screen.getByRole('link', { name: 'Westbridge Airport — AI Assistant' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('heading', { name: 'How can we help you?' }),
    ).toBeVisible();
    expect(
      screen.getByText(/Airport service information is currently unavailable/),
    ).toBeVisible();
  });
  it('starts in the explicit supported language', () => {
    mount('/?lang=id');
    expect(document.documentElement.lang).toBe('id');
    expect(screen.getByRole('button', { name: 'English' })).toBeInTheDocument();
  });
  it('switches locale with safe query/hash state and replaces history', async () => {
    const router = mount('/?topic=booking&email=private#details');
    await act(async () =>
      fireEvent.click(screen.getByRole('button', { name: 'Bahasa Indonesia' })),
    );
    expect(document.documentElement.lang).toBe('id');
    expect(router.state.location.search).toBe('?topic=booking&lang=id');
    expect(router.state.location.hash).toBe('#details');
    expect(router.state.historyAction).toBe('REPLACE');
  });
  it('gates an unknown host before routing', () => {
    render(
      <App
        bootstrap={createBootstrap(
          'unknown.example',
          '?host=localhost&lang=id',
        )}
      />,
    );
    expect(screen.getByRole('alert')).toHaveTextContent(
      'has not been configured',
    );
    expect(screen.queryByText(/Westbridge/)).not.toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(document.title).toBe('Site unavailable — AI Assistant');
  });
});

describe('C-01 customer home', () => {
  it.each(['information', 'booking', 'payment', 'contact'])(
    'opens %s without requiring sign-in or AI',
    async (topic) => {
      const router = mount();
      const cards = screen.getByRole('region', { name: 'Choose a help topic' });
      const link = within(cards)
        .getAllByRole('link')
        .find(
          (element) =>
            element.getAttribute('href') === `/help/new?topic=${topic}&lang=en`,
        );
      expect(link).toBeDefined();
      await act(async () => fireEvent.click(link!));
      expect(router.state.location.pathname).toBe('/help/new');
      expect(router.state.location.search).toBe(`?topic=${topic}&lang=en`);
      expect(
        await screen.findByRole('heading', { name: 'Customer support' }),
      ).toBeVisible();
    },
  );
  it('searches reviewed product guides locally, preserves text across language change and never puts it in URLs', async () => {
    const router = mount();
    const input = screen.getByRole('searchbox');
    fireEvent.change(input, { target: { value: '<private-no-match>' } });
    fireEvent.submit(screen.getByRole('search'));
    expect(screen.getByText('No matching guides.')).toBeVisible();
    expect(
      screen.getByRole('heading', { name: 'About this assistant' }),
    ).toHaveFocus();
    await act(async () =>
      fireEvent.click(screen.getByRole('button', { name: 'Bahasa Indonesia' })),
    );
    expect(screen.getByRole('searchbox')).toHaveValue('<private-no-match>');
    expect(screen.getByText('Tidak ada panduan yang cocok.')).toBeVisible();
    expect(router.state.location.search).toBe('?lang=id');
    fireEvent.click(screen.getByRole('button', { name: 'Lihat semua' }));
    expect(screen.getByRole('searchbox')).toHaveValue('');
    expect(document.querySelectorAll('details')).toHaveLength(4);
  });
  it('filters guides with meaningful pressed state and leaves direct support available', () => {
    mount();
    fireEvent.click(screen.getByRole('button', { name: 'Payments' }));
    expect(screen.getByRole('button', { name: 'Payments' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(document.querySelectorAll('details')).toHaveLength(1);
    expect(
      screen.getByText('Can I pay or request a transaction check here?'),
    ).toBeVisible();
    expect(
      screen.getAllByRole('link', { name: 'Contact support' }).length,
    ).toBeGreaterThan(0);
  });
});
