import { expect, it } from 'vitest';
import {
  decodeDeliveries,
  decodeReplies,
  deliveryStates,
} from '../../src/features/staff/mail-contracts';
import { delivery, reply, emailId } from './mail-fixture';
it('decodes every actual delivery state with nullable metadata and no synthetic receipt/body', () => {
  const data = {
    items: deliveryStates.map((state, i) => ({
      ...delivery(i + 1),
      delivery_status: state,
    })),
    has_more: false,
  };
  expect(decodeDeliveries(data).items).toHaveLength(7);
  expect(decodeDeliveries({ items: [], has_more: false }).items).toEqual([]);
});
it('honors latest100/has_more shape and duplicate IDs without inventing a cursor', () => {
  expect(
    decodeDeliveries({
      items: Array.from({ length: 100 }, (_, i) => delivery(i + 1)),
      has_more: true,
    }).has_more,
  ).toBe(true);
  for (const value of [
    { items: [delivery()], has_more: true },
    { items: [delivery(), delivery()], has_more: false },
    { items: [], has_more: false, cursor: 'invented' },
  ])
    expect(() => decodeDeliveries(value)).toThrow();
});
it('allows null body and published app text but refuses any staging/nonpublished body', () => {
  expect(decodeReplies([{ ...reply(), body: null }])[0]?.body).toBeNull();
  expect(
    decodeReplies([
      { ...reply(), state: 'pending', app_message_id: null, body: null },
    ])[0]?.state,
  ).toBe('pending');
  expect(() =>
    decodeReplies([
      {
        ...reply(),
        state: 'pending',
        app_message_id: null,
        body: 'RAW STAGING',
      },
    ]),
  ).toThrow();
  expect(() => decodeReplies([{ ...reply(), app_message_id: null }])).toThrow();
});
it('rejects raw email fields, malformed IDs/timestamps/states and oversized replies', () => {
  expect(() =>
    decodeDeliveries({
      items: [{ ...delivery(), recipient: 'private@example.com' }],
      has_more: false,
    }),
  ).toThrow();
  for (const patch of [
    { raw_headers: 'SECRET' },
    { created_at: 'yesterday' },
    { body: 'x'.repeat(4001) },
    { state: 'unknown-internal' },
    { email_id: 'invalid' },
    { attempts: 6 },
  ])
    expect(() => decodeReplies([{ ...reply(), ...patch }])).toThrow();
  expect(() =>
    decodeReplies(
      Array.from({ length: 101 }, (_, i) => ({
        ...reply(),
        email_id: emailId(i + 1),
      })),
    ),
  ).toThrow();
  expect(() => decodeReplies([reply(), reply()])).toThrow();
});
