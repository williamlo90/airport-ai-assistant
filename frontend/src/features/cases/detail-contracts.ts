import { z } from 'zod';
export const caseStatuses = [
  'new',
  'in_progress',
  'awaiting_customer',
  'awaiting_external',
  'closed',
] as const;
export const nextActors = ['staff', 'customer', 'external', 'none'] as const;
export const closureReasons = [
  'resolved',
  'customer_cancelled',
  'duplicate',
  'no_response',
] as const;
const id = z.uuid();
const reference = z.string().regex(/^CASE-[a-f0-9]{32}$/);
const timestamp = z
  .string()
  .regex(/^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/)
  .refine((v) => Number.isFinite(Date.parse(v)));
const version = z.number().int().min(1).max(Number.MAX_SAFE_INTEGER);
const text = z
  .string()
  .min(1)
  .refine((v) => !!v.trim() && !v.includes('\0'));
const historySchema = z
  .object({
    event_type: z.enum([
      'created',
      'identity_verified',
      'assigned',
      'status_changed',
      'reopened',
      'email_received',
      'customer_information',
    ]),
    previous_version: z.number().int().min(0),
    resulting_version: version,
    from_status: z.enum(caseStatuses).nullable(),
    to_status: z.enum(caseStatuses).nullable(),
    next_actor: z.enum(nextActors).nullable(),
    next_action: z.string().nullable(),
    close_reason: z.enum(closureReasons).nullable(),
    resolution: z.string().nullable(),
    customer_message: z.string().nullable(),
    created_at: timestamp,
  })
  .refine((h) => h.resulting_version === h.previous_version + 1);
const schema = z
  .object({
    id,
    public_reference: reference,
    status: z.enum(caseStatuses),
    summary: text,
    assigned_membership_id: id.nullable(),
    next_actor: z.enum(nextActors),
    next_action: text,
    version,
    updated_at: timestamp,
    category: z.enum(['information', 'booking_issue', 'transaction_issue']),
    service_type: z.enum(['general', 'parking', 'lounge']),
    customer_report: text,
    booking_reference: z.string().nullable(),
    transaction_reference: z.string().nullable(),
    language: z.enum(['id', 'en']),
    close_reason: z.enum(closureReasons).nullable(),
    resolution: z.string().nullable(),
    related_case_ref: reference.nullable().optional(),
    closed_at: timestamp.nullable(),
    history: z.array(historySchema),
  })
  .refine((c) =>
    c.history.every(
      (h, i) =>
        h.resulting_version <= c.version &&
        (i === 0 || h.resulting_version > c.history[i - 1]!.resulting_version),
    ),
  )
  .refine(
    (c) =>
      c.status !== 'closed' ||
      (c.close_reason !== null &&
        !!c.resolution?.trim() &&
        c.closed_at !== null),
  );
export type CaseDetail = z.infer<typeof schema>;
export type CaseHistory = CaseDetail['history'][number];
export function decodeCase(value: unknown): CaseDetail {
  return schema.parse(value);
}
export const continuationFormSchema = z.object({
  message: text.max(4000),
  sameIssue: z.boolean().refine((v) => v),
});
