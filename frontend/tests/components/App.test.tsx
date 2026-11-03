import { act, fireEvent, render, screen } from '@testing-library/react';
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
  it('mounts accessible identity and honest readiness', () => {
    mount();
    expect(
      screen.getByRole('heading', {
        name: 'Westbridge Airport — AI Assistant',
      }),
    ).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent(
      'Customer support screens are being prepared.',
    );
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
