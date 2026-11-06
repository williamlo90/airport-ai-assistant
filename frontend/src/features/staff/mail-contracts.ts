import { z } from 'zod';
export const deliveryStates = [
  'queued',
  'sending',
  'accepted',
  'delivered',
  'bounced',
  'failed',
  'delivery_unknown',
] as const;
const id = z.uuid();
const time = z.string().datetime({ offset: true });
const code = z
  .string()
  .min(1)
  .max(200)
  .refine((v) => !v.includes('\0'));
const delivery = z
  .object({
    job_id: id,
    app_message_id: id.nullable(),
    email_message_id: id.nullable(),
    delivery_status: z.enum(deliveryStates),
    delivery_version: z
      .number()
      .int()
      .positive()
      .max(Number.MAX_SAFE_INTEGER)
      .nullable(),
    needs_review: z.boolean(),
    review_reason: code.nullable(),
    queued_at: time,
    provider_status_at: time.nullable(),
    last_checked_at: time.nullable(),
  })
  .strict();
const deliveries = z
  .object({ items: z.array(delivery).max(100), has_more: z.boolean() })
  .strict();
const reply = z
  .object({
    email_id: id,
    state: z.enum([
      'pending',
      'publishing',
      'published',
      'review',
      'rejected',
      'challenged',
      'verification_reply',
    ]),
    reason: code.nullable(),
    app_message_id: id.nullable(),
    created_at: time,
    body: z
      .string()
      .refine((v) => !v.includes('\0') && [...v].length <= 4000)
      .nullable(),
    verification_state: z
      .enum(['pending', 'expired', 'consumed', 'failed'])
      .nullable(),
    send_state: z
      .enum(['queued', 'reserved', 'accepted', 'failed', 'delivery_unknown'])
      .nullable(),
    attempts: z.number().int().min(0).max(5).nullable(),
  })
  .strict()
  .refine((v) =>
    v.state === 'published'
      ? v.app_message_id !== null
      : v.app_message_id === null && v.body === null,
  );
const replies = z.array(reply).max(100);
export type DeliveryList = z.infer<typeof deliveries>;
export type EmailReply = z.infer<typeof reply>;
export function decodeDeliveries(value: unknown): DeliveryList {
  const result = deliveries.parse(value);
  if (
    (result.has_more && result.items.length !== 100) ||
    new Set(result.items.map((item) => item.job_id)).size !==
      result.items.length
  )
    throw Error('Invalid delivery page');
  return result;
}
export function decodeReplies(value: unknown): EmailReply[] {
  const result = replies.parse(value);
  if (new Set(result.map((item) => item.email_id)).size !== result.length)
    throw Error('Duplicate email reply');
  return result;
}
