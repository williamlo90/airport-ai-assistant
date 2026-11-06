import { fireEvent, render, screen } from '@testing-library/react';
import { createMemoryRouter } from 'react-router';
import { afterEach, expect, it } from 'vitest';
import { App } from '../../src/app/App';
import { createBootstrap } from '../../src/app/config/publicSite';
import { createAppRoutes } from '../../src/app/routing/routes';
import { fixture } from './fixture';
const owned: (() => void)[] = [];
afterEach(() => owned.splice(0).forEach((f) => f()));
async function mount() {
  const f = await fixture();
  owned.push(() => f.dispose());
  const b = createBootstrap('localhost', '');
  if (b.status !== 'configured') throw Error();
  const router = createMemoryRouter(
    createAppRoutes(b.site, undefined, [], f.auth),
    { initialEntries: ['/admin/information?lang=en'] },
  );
  owned.push(() => router.dispose());
  render(<App bootstrap={b} router={router} />);
  fireEvent.click(
    await screen.findByRole('button', { name: 'Load service information' }),
  );
  await screen.findByText('Waiting information');
  return f;
}
it('actual route uses scoped discovery, publication review then explicit confirm only', async () => {
  const f = await mount();
  fireEvent.click(screen.getByRole('button', { name: 'Open information' }));
  await screen.findByText('Visitors may wait in the arrivals hall.');
  fireEvent.click(
    screen.getByRole('button', { name: 'Review search indexing' }),
  );
  expect(f.calls.filter((c) => c.method === 'POST')).toHaveLength(0);
  fireEvent.click(
    screen.getByRole('button', { name: 'Confirm reviewed action' }),
  );
  await screen.findByText(/Search index: Ready for this content/);
  fireEvent.click(screen.getByRole('button', { name: 'Review publication' }));
  expect(
    screen.getByRole('region', { name: 'Review information change' }),
  ).toHaveFocus();
  expect(f.calls.filter((c) => c.method === 'POST')).toHaveLength(1);
  fireEvent.click(
    screen.getByRole('button', { name: 'Confirm reviewed action' }),
  );
  await screen.findByText('Status: published');
  expect(f.calls.filter((c) => c.method === 'POST')).toHaveLength(2);
});
it('new information form shows original metadata in review and cancels without a write', async () => {
  const f = await mount();
  fireEvent.click(
    screen.getByRole('button', { name: 'New service information' }),
  );
  fireEvent.change(screen.getByLabelText('Title'), {
    target: { value: 'New parking guidance' },
  });
  fireEvent.change(screen.getByLabelText(/Stable reference/), {
    target: { value: 'parking-guidance' },
  });
  fireEvent.change(screen.getByLabelText('Information text'), {
    target: { value: 'Use the public pickup area.' },
  });
  fireEvent.change(screen.getByLabelText(/Official source/), {
    target: { value: 'urn:airport:parking:pickup' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Review information' }));
  expect(
    screen.getByRole('region', { name: 'Review information change' }),
  ).toHaveTextContent('Use the public pickup area.');
  expect(f.calls.filter((c) => c.method === 'POST')).toHaveLength(0);
  fireEvent.click(screen.getByRole('button', { name: 'Back without saving' }));
  expect(screen.getByLabelText('Information text')).toHaveValue(
    'Use the public pickup area.',
  );
});

it('body-only draft edit preserves exact aware effective and booking instants in review and PATCH', async () => {
  const f = await mount();
  Object.assign(f.state.version, {
    valid_from: '2026-01-01T10:00:12.345678Z',
    valid_to: '2026-01-01T10:00:45.678901Z',
    applicability: {
      terminal: null,
      booking_created_from: '2025-01-01T10:00:12.345678Z',
      booking_created_to: '2025-01-01T10:00:45.678901Z',
    },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Open information' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Edit draft' }));
  fireEvent.change(screen.getByLabelText('Information text'), {
    target: { value: 'Only the wording changes.' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Review information' }));
  expect(
    screen.getByRole('region', { name: 'Review information change' }),
  ).toHaveTextContent('2026-01-01T10:00:12.345678Z');
  fireEvent.click(
    screen.getByRole('button', { name: 'Confirm reviewed action' }),
  );
  await screen.findByText(
    'Your reviewed action was saved. Current records are shown below.',
  );
  const sent = f.calls.find((c) => c.method === 'PATCH')!.body!
    .content as Record<string, unknown>;
  expect(sent.valid_from).toBe('2026-01-01T10:00:12.345678Z');
  expect(sent.valid_to).toBe('2026-01-01T10:00:45.678901Z');
  expect(sent.applicability).toEqual(f.state.version.applicability);
});

it.each(['2026-01-01T10:00:00.000123Z', '2026-01-01T10:00:12.120456Z'])(
  'unchanged normalized native datetime preserves submillisecond instant %s',
  async (instant) => {
    const f = await mount();
    f.state.version.valid_from = instant;
    fireEvent.click(screen.getByRole('button', { name: 'Open information' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Edit draft' }));
    fireEvent.change(screen.getByLabelText('Information text'), {
      target: { value: 'Only the wording changes.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Review information' }));
    expect(
      screen.getByRole('region', { name: 'Review information change' }),
    ).toHaveTextContent(instant);
    fireEvent.click(
      screen.getByRole('button', { name: 'Confirm reviewed action' }),
    );
    await screen.findByText(
      'Your reviewed action was saved. Current records are shown below.',
    );
    expect(
      (
        f.calls.find((c) => c.method === 'PATCH')!.body!.content as Record<
          string,
          unknown
        >
      ).valid_from,
    ).toBe(instant);
  },
);
