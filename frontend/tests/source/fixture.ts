import { fixture, json, error, OTHER } from '../chat/fixture';
import { SourceStore } from '../../src/features/source/store';
import type {
  Booking,
  Transaction,
  TransactionPage,
} from '../../src/features/source/contracts';
export { json, error };
export const BOOK = 'PK-A-001',
  TX = 'TX-A-001';
export const booking = (): Booking => ({
  reference: BOOK,
  service_type: 'parking',
  status: 'confirmed',
  start_at: '2026-11-10T02:00:00Z',
  end_at: '2026-11-11T02:00:00Z',
  service_timezone: 'Asia/Jakarta',
  location: 'Terminal 2 Parking',
  policy_reference: 'parking-v3',
  allowed_scopes: ['booking:read'],
  source_updated_at: '2026-11-02T01:00:00Z',
  observed_at: '2026-11-04T02:00:00Z',
});
export const details = (): Transaction => ({
  reference: TX,
  status: 'paid',
  allowed_scopes: ['payment_status:read', 'payment_details:read'],
  booking_reference: BOOK,
  currency: 'SGD',
  amount_minor: 150050,
  currency_exponent: 2,
  refund_status: 'none',
  source_updated_at: '2026-11-02T01:00:00Z',
  observed_at: '2026-11-04T02:01:00Z',
});
export const statusOnly = (): Transaction => ({
  reference: TX,
  status: 'unknown',
  allowed_scopes: ['payment_status:read'],
  source_status_label: 'awaiting_review',
  source_updated_at: '2026-11-02T01:00:00Z',
  observed_at: '2026-11-04T02:01:00Z',
});
export function sourceFixture() {
  const f = fixture();
  f.server.kind = 'customer';
  f.server.principal = OTHER;
  const base = f.fetcher.getMockImplementation()!;
  const source = {
    booking: booking(),
    transaction: details(),
    page: { items: [details()], next_cursor: null } as TransactionPage,
    paths: [] as string[],
    handler: null as
      null | ((path: string, init?: RequestInit) => Promise<Response> | null),
  };
  f.fetcher.mockImplementation(async (input, init) => {
    const path = String(input);
    if (
      !path.startsWith('/api/v1/bookings/') &&
      !path.startsWith('/api/v1/transactions/')
    )
      return base(input, init);
    source.paths.push(path);
    const custom = source.handler?.(path, init);
    if (custom) return custom;
    if (path.includes('/transactions?')) return json(source.page);
    if (path.startsWith('/api/v1/transactions/'))
      return json({ ...source.transaction, reference: path.split('/').at(-1) });
    return json({ ...source.booking, reference: path.split('/').at(-1) });
  });
  const sources = new SourceStore(f.auth, 'test-airport'),
    off = sources.connect();
  return {
    ...f,
    sources,
    source,
    async ready() {
      await f.auth.start();
    },
    async search(
      kind: 'booking' | 'transaction' = 'booking',
      reference = BOOK,
    ) {
      sources.changeInput({ kind, reference });
      await sources.lookup();
    },
    dispose() {
      off();
      sources.dispose();
      f.dispose();
    },
  };
}
