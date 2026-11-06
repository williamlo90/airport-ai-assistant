import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { StrictMode } from 'react';
import { afterEach, expect, it } from 'vitest';
import { MessageCitations } from '../../src/features/chat/MessageCitations';
import { fixture, ID, OTHER, json, error } from './fixture';

const owned: ReturnType<typeof fixture>[] = [];
afterEach(() => owned.splice(0).forEach((f) => f.dispose()));
const receipt = () => ({
  message_id: OTHER,
  status: 'historical_publication',
  issued_at: '2026-11-05T12:00:00Z',
  citations: [
    {
      version_id: ID,
      generation: OTHER,
      ordinal: 0,
      basis_hash: 'a'.repeat(64),
      document_id: ID,
      document_revision: 1,
      quote: '<b>Original policy text</b>',
      source_uri: 'https://airport.example/policy',
    },
    {
      version_id: ID,
      generation: OTHER,
      ordinal: 1,
      basis_hash: 'a'.repeat(64),
      document_id: ID,
      document_revision: 1,
      quote: 'Managed policy excerpt.',
      source_uri: 'urn:airport:parking-policy',
    },
  ],
});
async function setup(locale: 'en' | 'id' = 'en') {
  const f = fixture();
  owned.push(f);
  await f.auth.start();
  f.server.override = async () => json(receipt());
  const props = {
    auth: f.auth,
    conversationId: ID,
    messageId: OTHER,
    generationEpoch: 0,
    locale,
  };
  const view = render(
    <StrictMode>
      <MessageCitations {...props} />
    </StrictMode>,
  );
  return { ...f, props, ...view };
}
it('loads only after disclosure, labels history, renders text safely and distinguishes HTTPS from URN', async () => {
  const f = await setup();
  expect(f.fetcher).toHaveBeenCalledTimes(1);
  const button = screen.getByRole('button', { name: 'Sources' });
  button.focus();
  expect(button).toHaveFocus();
  expect(button).toHaveAttribute('aria-expanded', 'false');
  expect(
    document.getElementById(button.getAttribute('aria-controls')!),
  ).not.toBeVisible();
  fireEvent.click(button);
  expect(await screen.findByText('<b>Original policy text</b>')).toBeVisible();
  expect(f.container.querySelector('blockquote b')).toBeNull();
  expect(screen.getByText(/Policies may have changed/)).toBeVisible();
  expect(f.container.querySelector('time')).toHaveAttribute(
    'datetime',
    '2026-11-05T12:00:00Z',
  );
  const link = screen.getByRole('link');
  expect(link).toHaveAttribute('href', 'https://airport.example/policy');
  expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  expect(screen.getByText('urn:airport:parking-policy').tagName).toBe('SPAN');
  expect(button).toHaveFocus();
  fireEvent.click(button);
  expect(
    screen.queryByText('<b>Original policy text</b>'),
  ).not.toBeInTheDocument();
  expect(button).toHaveAttribute('aria-expanded', 'false');
});
it.each([
  ['en', 'Sources', 'Sources checked:'],
  ['id', 'Sumber', 'Sumber diperiksa:'],
] as const)(
  'labels evidence collection time accurately in %s',
  async (locale, button, label) => {
    const f = await setup(locale);
    fireEvent.click(screen.getByRole('button', { name: button }));
    expect(await screen.findByText(label, { exact: false })).toBeVisible();
    expect(
      screen.queryByText(/Published:|Diterbitkan:/),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(
        /when this answer was published|saat jawaban ini diterbitkan/,
      ),
    ).not.toBeInTheDocument();
    expect(f.container.querySelector('time')).toHaveAttribute(
      'datetime',
      receipt().issued_at,
    );
  },
);
it('accepts an empty receipt with no fabricated source, date or policy claim in Indonesian', async () => {
  const f = await setup('id');
  f.server.override = async () =>
    json({
      message_id: OTHER,
      status: 'historical_publication',
      issued_at: null,
      citations: [],
    });
  fireEvent.click(screen.getByRole('button', { name: 'Sumber' }));
  expect(
    await screen.findByText(
      'Tidak ada sitasi sumber yang tercatat untuk pesan ini.',
    ),
  ).toBeVisible();
  expect(screen.queryByRole('link')).not.toBeInTheDocument();
  expect(f.container.querySelector('time')).toBeNull();
});
it('503 offers explicit retry, with no invented answer and no POST', async () => {
  const f = await setup();
  f.server.override = async () => error(503, 'AGENT_UNAVAILABLE');
  fireEvent.click(screen.getByRole('button', { name: 'Sources' }));
  expect(
    await screen.findByText('The service is temporarily unavailable.'),
  ).toBeVisible();
  expect(screen.queryByRole('link')).not.toBeInTheDocument();
  f.server.override = async () => json(receipt());
  fireEvent.click(screen.getByRole('button', { name: 'Retry sources' }));
  expect(await screen.findByText('Managed policy excerpt.')).toBeVisible();
  expect(f.fetcher.mock.calls.every(([, init]) => init?.method === 'GET')).toBe(
    true,
  );
});
it('clears synchronously on route resource or generation epoch change and does not fetch on rerender', async () => {
  const f = await setup();
  fireEvent.click(screen.getByRole('button', { name: 'Sources' }));
  await screen.findByText('Managed policy excerpt.');
  f.rerender(
    <StrictMode>
      <MessageCitations {...f.props} generationEpoch={1} />
    </StrictMode>,
  );
  expect(screen.queryByText('Managed policy excerpt.')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Sources' })).toHaveAttribute(
    'aria-expanded',
    'false',
  );
  expect(f.fetcher).toHaveBeenCalledTimes(2);
  f.rerender(
    <StrictMode>
      <MessageCitations {...f.props} conversationId={OTHER} messageId={ID} />
    </StrictMode>,
  );
  expect(screen.queryByRole('link')).not.toBeInTheDocument();
  expect(f.fetcher).toHaveBeenCalledTimes(2);
});
it('auth invalidation removes citations before a late response can expose them', async () => {
  const f = await setup();
  let resolve!: (value: Response) => void;
  f.server.override = () =>
    new Promise<Response>((r) => {
      resolve = r;
    });
  fireEvent.click(screen.getByRole('button', { name: 'Sources' }));
  f.server.override = async () => error(401, 'AUTH_REQUIRED');
  act(() => {
    f.server.proof = 'q'.repeat(43);
    expect(() => f.auth.capture()).toThrow();
  });
  expect(
    screen.queryByRole('button', { name: 'Sources' }),
  ).not.toBeInTheDocument();
  await act(async () => {
    resolve(json(receipt()));
  });
  await waitFor(() =>
    expect(
      screen.queryByText('Managed policy excerpt.'),
    ).not.toBeInTheDocument(),
  );
});
