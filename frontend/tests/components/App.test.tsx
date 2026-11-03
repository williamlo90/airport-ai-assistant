import { fireEvent, render, screen } from '@testing-library/react';
import { StrictMode } from 'react';
import { beforeEach, describe, expect, it } from 'vitest';
import { App } from '../../src/app/App';
import { createBootstrap } from '../../src/app/config/publicSite';

beforeEach(() => window.history.replaceState(null, '', '/'));

describe('frontend bootstrap', () => {
  it('mounts the application with an accessible identity and honest readiness state', () => {
    render(
      <StrictMode>
        <App bootstrap={createBootstrap('localhost', '')} />
      </StrictMode>,
    );

    expect(
      screen.getByRole('heading', {
        name: 'Westbridge Airport — AI Assistant',
        level: 1,
      }),
    ).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent(
      'Customer support screens are being prepared.',
    );
  });

  it('starts in Indonesian only for the explicit supported language', () => {
    render(<App bootstrap={createBootstrap('localhost', '?lang=id')} />);
    expect(document.documentElement.lang).toBe('id');
    expect(screen.getByRole('status')).toHaveTextContent(
      'Frontend sudah berjalan.',
    );
    expect(screen.getByRole('button', { name: 'English' })).toBeInTheDocument();
  });

  it('switches labels and the document language while preserving unrelated URL state', () => {
    window.history.replaceState(null, '', '/?topic=booking#details');
    render(<App bootstrap={createBootstrap('127.0.0.1', '?topic=booking')} />);
    fireEvent.click(screen.getByRole('button', { name: 'Bahasa Indonesia' }));
    expect(document.documentElement.lang).toBe('id');
    expect(new URLSearchParams(window.location.search).get('lang')).toBe('id');
    expect(new URLSearchParams(window.location.search).get('topic')).toBe(
      'booking',
    );
    expect(window.location.hash).toBe('#details');
    fireEvent.click(screen.getByRole('button', { name: 'English' }));
    expect(document.documentElement.lang).toBe('en');
    expect(document.title).toBe('Westbridge Airport — AI Assistant');
  });

  it('does not render a tenant identity or controls on an unconfigured host', () => {
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
    expect(document.documentElement.lang).toBe('en');
  });
});
