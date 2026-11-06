import { act, fireEvent, render, screen } from '@testing-library/react';
import { StrictMode } from 'react';
import { afterEach, expect, it } from 'vitest';
import { StaffMail } from '../../src/features/staff/StaffMail';
import { deliveryStates } from '../../src/features/staff/mail-contracts';
import { denied } from '../../src/dev/staff-fixtures';
import { mailFixture, caseId, delivery, reply } from './mail-fixture';
const owned: (() => void)[] = [];
afterEach(() => owned.splice(0).forEach((dispose) => dispose()));
async function setup(locale: 'en' | 'id' = 'en') {
  const f = await mailFixture();
  const view = render(
    <StrictMode>
      <StaffMail queue={f.queue} caseId={caseId(1)} locale={locale} />
    </StrictMode>,
  );
  owned.push(() => {
    view.unmount();
    f.dispose();
  });
  return { ...f, ...view };
}
it('has explicit read-only load and distinct delivery states; accepted and delivered never claim read', async () => {
  const f = await setup();
  f.feed.deliveries.items = deliveryStates.map((state, i) => ({
    ...delivery(i + 1),
    delivery_status: state,
    needs_review: state === 'delivery_unknown',
    review_reason: 'INTERNAL SECRET',
  }));
  const before = f.server.requests.length;
  const button = screen.getByRole('button', { name: 'Load email activity' });
  button.focus();
  expect(button).toHaveFocus();
  expect(f.server.requests).toHaveLength(before);
  fireEvent.click(button);
  expect(
    await screen.findByText('Delivery not confirmed', { exact: true }),
  ).toBeVisible();
  for (const name of [
    'Queued',
    'Sending',
    'Accepted by provider',
    'Delivered',
    'Bounced',
    'Failed',
  ])
    expect(screen.getByText(name, { exact: true })).toBeVisible();
  expect(
    screen.getByText(/Delivery does not prove the email was read/),
  ).toBeVisible();
  expect(screen.queryByText('INTERNAL SECRET')).not.toBeInTheDocument();
  expect(screen.queryByText(delivery().job_id)).not.toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: /send|resend|translate/i }),
  ).not.toBeInTheDocument();
  expect(f.server.requests.every((r) => r.method === 'GET')).toBe(true);
});
it('published body is plain text; pending metadata and null text do not expose staging or invent email loss', async () => {
  const f = await setup();
  f.feed.replies = [
    reply(),
    {
      ...reply(2),
      state: 'pending',
      app_message_id: null,
      body: null,
      reason: 'SECRET STAGING REASON',
    },
  ];
  fireEvent.click(screen.getByRole('button', { name: 'Load email activity' }));
  expect(
    await screen.findByText('<b>Original customer email</b>'),
  ).toBeVisible();
  expect(f.container.querySelector('b')).toBeNull();
  expect(screen.getByText('Processing pending')).toBeVisible();
  expect(
    screen.getByText(/Message text is not available in this authorized view/),
  ).toBeVisible();
  expect(screen.queryByText('SECRET STAGING REASON')).not.toBeInTheDocument();
});
it('renders both100-item limits honestly, without cursor controls or a complete-history claim', async () => {
  const f = await setup();
  f.feed.deliveries = {
    items: Array.from({ length: 100 }, (_, i) => delivery(i + 1)),
    has_more: true,
  };
  f.feed.replies = Array.from({ length: 100 }, (_, i) => reply(i + 1));
  fireEvent.click(screen.getByRole('button', { name: 'Load email activity' }));
  expect(
    await screen.findByText(
      'More sends exist. Additional pages are not available in this view.',
    ),
  ).toBeVisible();
  expect(
    await screen.findByText(
      'The reply limit was reached. The service does not provide a continuation indicator.',
    ),
  ).toBeVisible();
  expect(screen.getByText(/This is not a complete email thread/)).toBeVisible();
  expect(
    screen.queryByRole('button', { name: /next page|load more/i }),
  ).not.toBeInTheDocument();
});
it('Indonesian empty lists and scope reset remain truthful and clear old text immediately', async () => {
  const f = await setup('id');
  f.feed.deliveries.items = [];
  f.feed.replies = [];
  fireEvent.click(screen.getByRole('button', { name: 'Muat aktivitas email' }));
  expect(
    await screen.findByText(
      'Tidak ada balasan masuk yang dikembalikan untuk kasus ini.',
    ),
  ).toBeVisible();
  expect(
    screen.getByText(
      'Tidak ada kiriman keluar yang dikembalikan untuk kasus ini.',
    ),
  ).toBeVisible();
  await act(async () => {
    await f.auth.recheck();
  });
  expect(
    screen.queryByText(
      'Tidak ada balasan masuk yang dikembalikan untuk kasus ini.',
    ),
  ).not.toBeInTheDocument();
  expect(
    screen.getByText(
      'Aktivitas email disembunyikan sampai akses kasus terkini terverifikasi.',
    ),
  ).toBeVisible();
});
it('denied mail requires explicit case-access refresh before another explicit email load', async () => {
  const f = await setup();
  const original = f.server.custom!;
  f.server.custom = (path, init) =>
    path.endsWith('/email-replies')
      ? Promise.resolve(denied(404))
      : original(path, init);
  fireEvent.click(screen.getByRole('button', { name: 'Load email activity' }));
  const refresh = await screen.findByRole('button', {
    name: 'Refresh case access',
  });
  expect(
    screen.queryByText('<b>Original customer email</b>'),
  ).not.toBeInTheDocument();
  const requests = f.server.requests.filter((r) =>
    /\/email-(replies|deliveries)$/.test(r.path),
  ).length;
  await act(async () => {
    fireEvent.click(refresh);
  });
  expect(
    await screen.findByRole('button', { name: 'Load email activity' }),
  ).toBeEnabled();
  expect(
    f.server.requests.filter((r) =>
      /\/email-(replies|deliveries)$/.test(r.path),
    ),
  ).toHaveLength(requests);
  f.server.custom = original;
  fireEvent.click(screen.getByRole('button', { name: 'Load email activity' }));
  expect(
    await screen.findByText('<b>Original customer email</b>'),
  ).toBeVisible();
  expect(f.server.requests.every((r) => r.method === 'GET')).toBe(true);
});
