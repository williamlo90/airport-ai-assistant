import { z } from 'zod';
import { record, uuid } from '../../api/contracts';

const text = (max: number) =>
  z
    .string()
    .min(1)
    .max(max)
    .refine((v) => !!v.trim() && !v.includes('\0'));
export const categories = [
  'information',
  'booking_issue',
  'transaction_issue',
] as const;
export const services = ['general', 'parking', 'lounge'] as const;
const ref = z
  .string()
  .regex(/^[A-Za-z0-9_-]{1,80}$/)
  .nullable();
const payloadSchema = z
  .object({
    category: z.enum(categories),
    service_type: z.enum(services),
    summary: text(200),
    customer_report: text(4000),
    booking_reference: ref,
    transaction_reference: ref,
    contact_email: z.email().max(320).nullable(),
    attachment_ids: z
      .array(z.uuid())
      .max(3)
      .refine((ids) => new Set(ids).size === ids.length),
  })
  .strict();
export type ReportPayload = z.infer<typeof payloadSchema>;
export const formSchema = z
  .object({
    category: z.enum(categories),
    service_type: z.enum(services),
    summary: text(200),
    customer_report: text(4000),
    contact_email: z.email().max(320),
    booking_reference: z.string().regex(/^(?:[A-Za-z0-9_-]{1,80})?$/),
    transaction_reference: z.string().regex(/^(?:[A-Za-z0-9_-]{1,80})?$/),
  })
  .strict();
export type ReportForm = z.infer<typeof formSchema>;
export type DraftAction = 'create_case' | 'request_handoff';
export const handoffFormSchema = formSchema.extend({
  contact_email: z.union([z.literal(''), z.email().max(320)]),
});
export const anonymousHandoffFormSchema = handoffFormSchema.extend({
  category: z.literal('information'),
  service_type: z.literal('general'),
  contact_email: z.literal(''),
  booking_reference: z.literal(''),
  transaction_reference: z.literal(''),
});
/** Mirrors the backend's narrow anonymous general-handoff exception. */
export function canUseDraft(
  kind: 'anonymous' | 'customer' | 'staff',
  action: DraftAction,
  payload: ReportPayload,
  caseId: string | null = null,
) {
  if (kind === 'customer') return true;
  return (
    kind === 'anonymous' &&
    action === 'request_handoff' &&
    caseId === null &&
    payload.category === 'information' &&
    payload.service_type === 'general' &&
    payload.booking_reference === null &&
    payload.transaction_reference === null &&
    payload.contact_email === null &&
    payload.attachment_ids.length === 0
  );
}
export function reportSchema(action: DraftAction, anonymous: boolean) {
  return action !== 'request_handoff'
    ? formSchema
    : anonymous
      ? anonymousHandoffFormSchema
      : handoffFormSchema;
}
export const emptyForm = (): ReportForm => ({
  category: 'information',
  service_type: 'general',
  summary: '',
  customer_report: '',
  contact_email: '',
  booking_reference: '',
  transaction_reference: '',
});
export function toPayload(
  form: ReportForm,
  action: DraftAction = 'create_case',
  anonymous = false,
  attachmentIds: string[] = [],
): ReportPayload {
  const v = reportSchema(action, anonymous).parse(form);
  return {
    ...v,
    contact_email: v.contact_email || null,
    booking_reference: v.booking_reference || null,
    transaction_reference: v.transaction_reference || null,
    attachment_ids: [...attachmentIds],
  };
}
export function toForm(payload: ReportPayload): ReportForm {
  return {
    category: payload.category,
    service_type: payload.service_type,
    summary: payload.summary,
    customer_report: payload.customer_report,
    contact_email: payload.contact_email ?? '',
    booking_reference: payload.booking_reference ?? '',
    transaction_reference: payload.transaction_reference ?? '',
  };
}
export interface DraftView {
  draft_id: string;
  conversation_id: string;
  action_type: 'create_case' | 'request_handoff';
  payload: ReportPayload;
  destination: {
    queue_id: string;
    organization_id: string;
    queue_name: string;
    organization_name: string;
    service_type: (typeof services)[number];
  };
  summary: string;
  language: 'en' | 'id';
  version: number;
  payload_hash: string;
  expires_at: string;
  state: 'pending' | 'cancelled' | 'expired' | 'committed';
  result_ref: string | null;
  case_id: string | null;
}
export function decodeDraft(value: unknown): DraftView {
  const d = record(value),
    dest = record(d.destination);
  if (
    !uuid(d.draft_id) ||
    !uuid(d.conversation_id) ||
    (d.action_type !== 'create_case' && d.action_type !== 'request_handoff') ||
    !uuid(dest.queue_id) ||
    !uuid(dest.organization_id) ||
    typeof dest.queue_name !== 'string' ||
    !dest.queue_name.trim() ||
    dest.queue_name.length > 200 ||
    typeof dest.organization_name !== 'string' ||
    !dest.organization_name.trim() ||
    dest.organization_name.length > 200 ||
    !services.includes(dest.service_type as (typeof services)[number]) ||
    typeof d.summary !== 'string' ||
    !d.summary.length ||
    d.summary.length > 7000 ||
    (d.language !== 'en' && d.language !== 'id') ||
    !Number.isSafeInteger(d.version) ||
    (d.version as number) < 1 ||
    typeof d.payload_hash !== 'string' ||
    !/^[a-f0-9]{64}$/.test(d.payload_hash) ||
    typeof d.expires_at !== 'string' ||
    !/^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/.test(d.expires_at) ||
    !Number.isFinite(Date.parse(d.expires_at)) ||
    typeof d.state !== 'string' ||
    !['pending', 'cancelled', 'expired', 'committed'].includes(d.state) ||
    (d.state === 'committed' && d.result_ref === null) ||
    (d.result_ref !== null &&
      (typeof d.result_ref !== 'string' ||
        !/^CASE-[a-f0-9]{32}$/.test(d.result_ref))) ||
    (d.case_id !== null && !uuid(d.case_id))
  )
    throw Error('Invalid draft DTO');
  return {
    draft_id: d.draft_id,
    conversation_id: d.conversation_id,
    action_type: d.action_type as DraftView['action_type'],
    payload: payloadSchema.parse(d.payload),
    destination: {
      queue_id: dest.queue_id,
      organization_id: dest.organization_id,
      queue_name: dest.queue_name,
      organization_name: dest.organization_name,
      service_type: dest.service_type as (typeof services)[number],
    },
    summary: d.summary,
    language: d.language,
    version: d.version as number,
    payload_hash: d.payload_hash,
    expires_at: d.expires_at,
    state: d.state as DraftView['state'],
    result_ref: d.result_ref as string | null,
    case_id: d.case_id as string | null,
  };
}
