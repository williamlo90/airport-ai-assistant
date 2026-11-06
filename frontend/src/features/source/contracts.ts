import { z } from 'zod';
import { formatTimestamp } from '../../locales/format';
export const referenceSchema = z.string().regex(/^[A-Za-z0-9_-]{1,80}$/);
export const lookupSchema = z.object({
  kind: z.enum(['booking', 'transaction']),
  reference: referenceSchema,
});
export type LookupInput = z.infer<typeof lookupSchema>;
const timestamp = z.iso
  .datetime({ offset: true })
  .refine((v) => /(?:Z|[+-]00:00)$/.test(v))
  .refine((v) => {
    try {
      formatTimestamp(v, 'en', 'UTC');
      return true;
    } catch {
      return false;
    }
  });
const observation = {
  reference: referenceSchema,
  source_updated_at: timestamp,
  observed_at: timestamp,
  source_status_label: z
    .string()
    .regex(/^[a-z0-9_]{1,64}$/)
    .optional(),
};
const bookingSchema = z
  .object({
    ...observation,
    service_type: z.enum(['parking', 'lounge']),
    status: z.enum(['confirmed', 'cancelled', 'pending', 'unknown']),
    start_at: timestamp,
    end_at: timestamp,
    service_timezone: z
      .string()
      .min(1)
      .max(64)
      .refine((v) => {
        try {
          new Intl.DateTimeFormat('en', { timeZone: v }).format(0);
          return true;
        } catch {
          return false;
        }
      }),
    location: z.string().min(1).max(200),
    policy_reference: referenceSchema,
    allowed_scopes: z.tuple([z.literal('booking:read')]),
  })
  .strict()
  .refine((v) => Date.parse(v.start_at) < Date.parse(v.end_at));
const transactionSchema = z
  .object({
    ...observation,
    status: z.enum(['pending', 'paid', 'failed', 'refunded', 'unknown']),
    allowed_scopes: z
      .array(z.enum(['payment_status:read', 'payment_details:read']))
      .min(1)
      .max(2),
    booking_reference: referenceSchema.optional(),
    currency: z.enum(['IDR', 'SGD']).optional(),
    amount_minor: z.number().int().min(0).max(1e12).optional(),
    currency_exponent: z.number().int().min(0).max(3).optional(),
    refund_status: z
      .enum(['none', 'pending', 'completed', 'unknown'])
      .optional(),
  })
  .strict()
  .superRefine((v, ctx) => {
    const scopes = new Set(v.allowed_scopes),
      fields = [
        'currency',
        'amount_minor',
        'currency_exponent',
        'refund_status',
      ] as const;
    const details = scopes.has('payment_details:read');
    if (
      scopes.size !== v.allowed_scopes.length ||
      !scopes.has('payment_status:read') ||
      (details
        ? fields.some((k) => v[k] === undefined) ||
          v.currency_exponent !== (v.currency === 'IDR' ? 0 : 2)
        : fields.some((k) => Object.hasOwn(v, k)))
    )
      ctx.addIssue({ code: 'custom', message: 'Invalid payment projection' });
  });
export type Booking = z.infer<typeof bookingSchema>;
export type Transaction = z.infer<typeof transactionSchema>;
export interface TransactionPage {
  items: Transaction[];
  next_cursor: string | null;
}
export const decodeBooking = (v: unknown): Booking => bookingSchema.parse(v);
export const decodeTransaction = (v: unknown): Transaction =>
  transactionSchema.parse(v);
export function decodePage(
  v: unknown,
  reference: string,
  limit: number,
): TransactionPage {
  const page = z
    .object({
      items: z.array(transactionSchema).max(limit),
      next_cursor: z.string().min(1).max(2048).nullable(),
    })
    .strict()
    .parse(v);
  if (
    new Set(page.items.map((t) => t.reference)).size !== page.items.length ||
    page.items.some(
      (t) =>
        t.booking_reference !== undefined && t.booking_reference !== reference,
    )
  )
    throw Error('Invalid transaction page');
  return page;
}
