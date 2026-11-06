import { z } from 'zod';
import { caseStatuses, nextActors } from './detail-contracts';

const date = z.iso.datetime({ offset: true });
const text = z
  .string()
  .min(1)
  .refine((v) => !!v.trim() && !v.includes('\0'));
export const customerCaseItem = z.object({
  id: z.uuid(),
  public_reference: z.string().regex(/^CASE-[a-f0-9]{32}$/),
  status: z.enum(caseStatuses),
  summary: text,
  next_actor: z.enum(nextActors),
  next_action: text,
  version: z.number().int().positive().max(Number.MAX_SAFE_INTEGER),
  created_at: date,
  updated_at: date,
});
const page = z
  .object({
    items: z.array(customerCaseItem).max(25),
    status: z.enum(caseStatuses).nullable(),
    limit: z.literal(25),
    sort: z.literal('created_asc'),
    has_more: z.boolean(),
    next_cursor: z
      .string()
      .regex(/^[A-Za-z0-9_-]+$/)
      .max(32768)
      .nullable(),
    watermark: date,
    consistency: z.literal('live_keyset'),
  })
  .superRefine((value, ctx) => {
    const ids = new Set<string>();
    if (
      value.has_more !== (value.next_cursor !== null) ||
      (value.has_more && value.items.length !== value.limit)
    )
      ctx.addIssue({ code: 'custom', message: 'Invalid page continuation' });
    for (let i = 0; i < value.items.length; i++) {
      const item = value.items[i]!,
        prior = value.items[i - 1];
      if (
        ids.has(item.id) ||
        (value.status !== null && item.status !== value.status) ||
        Date.parse(item.created_at) > Date.parse(value.watermark) ||
        (prior &&
          (Date.parse(prior.created_at) > Date.parse(item.created_at) ||
            (prior.created_at === item.created_at && prior.id >= item.id)))
      )
        ctx.addIssue({
          code: 'custom',
          message: 'Invalid page order or scope',
        });
      ids.add(item.id);
    }
  });
export type CustomerCasePage = z.infer<typeof page>;
export type CustomerCaseStatus = z.infer<typeof customerCaseItem>['status'];
export const decodeCustomerCases = (value: unknown): CustomerCasePage =>
  page.parse(value);

export const informationMessage = z
  .string()
  .refine(
    (v) =>
      !!v.trim() &&
      !v.includes('\0') &&
      Array.from(v).length <= 4000 &&
      !/[\uD800-\uDFFF]/u.test(v),
  );
const preview = z.object({
  preview_id: z.uuid(),
  case_id: z.uuid(),
  expected_version: z.number().int().positive(),
  message: informationMessage,
  language: z.enum(['en', 'id']),
  expires_at: z.number().int().positive(),
  confirmation_token: z.string().min(1).max(32768),
});
const receipt = z.object({
  state: z.literal('saved'),
  preview_id: z.uuid(),
  case_id: z.uuid(),
  message_id: z.uuid(),
  sequence: z.number().int().positive(),
  case_version: z.number().int().positive(),
});
export type InformationPreview = z.infer<typeof preview>;
export type InformationReceipt = z.infer<typeof receipt>;
export const decodeInformationPreview = (value: unknown) =>
  preview.parse(value);
export const decodeInformationReceipt = (value: unknown) =>
  receipt.parse(value);
