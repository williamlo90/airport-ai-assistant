import { render, screen } from '@testing-library/react';
import { StrictMode } from 'react';
import { describe, expect, it } from 'vitest';
import { App } from '../../src/app/App';

describe('frontend bootstrap', () => {
  it('mounts the application with an accessible identity and honest readiness state', () => {
    render(
      <StrictMode>
        <App />
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
});
