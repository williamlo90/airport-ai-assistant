import { z } from 'zod';
import type { Membership } from '../../app/auth/contracts';
import { caseStatuses, decodeCase } from '../cases/detail-contracts';
export { decodeCase };
export const views = ['unassigned', 'mine', 'all'] as const;
export interface QueueFilter {
  view: (typeof views)[number];
  queueId: string;
  status: '' | (typeof caseStatuses)[number];
}
export const defaultFilter = (): QueueFilter => ({
  view: 'unassigned',
  queueId: '',
  status: '',
});
const id = z.uuid(),
  version = z.number().int().min(1).max(Number.MAX_SAFE_INTEGER);
const text = z
  .string()
  .min(1)
  .refine((v) => !!v.trim() && !v.includes('\0'));
export function timeKey(v: string) {
  const m =
    /^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,6}))?(?:Z|\+00:00)$/.exec(v);
  if (
    !m ||
    !Number.isFinite(Date.parse(v)) ||
    new Date(v).toISOString().slice(0, 19) !== m[1]
  )
    throw Error('Invalid timestamp');
  return m[1] + '.' + (m[2] ?? '').padEnd(6, '0');
}
const timestamp = z.string().refine((v) => {
  try {
    timeKey(v);
    return true;
  } catch {
    return false;
  }
});
const label = z.object({ id, name: text.max(200) }).strict();
const item = z
  .object({
    id,
    public_reference: z.string().regex(/^CASE-[a-f0-9]{32}$/),
    status: z.enum(caseStatuses),
    summary: text.max(200),
    assigned_membership_id: id.nullable(),
    next_actor: z.enum(['staff', 'customer', 'external', 'none']),
    next_action: text.max(2000),
    version,
    updated_at: timestamp,
    queue_id: id,
    created_at: timestamp,
  })
  .strict();
const cursor = z
  .string()
  .min(1)
  .max(2048)
  .regex(/^[A-Za-z0-9_-]+$/);
const page = z
  .object({
    membership_id: id,
    membership_version: version,
    organization: label,
    queues: z.array(label).min(1),
    queue_id: id.nullable(),
    view: z.enum(views),
    status: z.enum(caseStatuses).nullable(),
    sort: z.literal('created_asc'),
    limit: z.literal(25),
    items: z.array(item).max(25),
    has_more: z.boolean(),
    next_cursor: cursor.nullable(),
    consistency: z.literal('live_keyset'),
    watermark: timestamp,
    observed_at: timestamp,
  })
  .strict();
export type QueuePage = z.infer<typeof page>;
export type QueueItem = QueuePage['items'][number];
export function decodeQueue(
  value: unknown,
  member: Membership,
  filter: QueueFilter,
  watermark?: string,
  priorCursor?: string | null,
): QueuePage {
  const p = page.parse(value),
    ids = new Set(p.queues.map((q) => q.id)),
    seen = new Set<string>();
  if (
    p.membership_id !== member.id ||
    p.membership_version !== member.version ||
    p.organization.id !== member.organization_id ||
    p.view !== filter.view ||
    p.queue_id !== (filter.queueId || null) ||
    p.status !== (filter.status || null) ||
    ids.size !== p.queues.length ||
    p.queues.some((q) => !member.queue_ids.includes(q.id)) ||
    (filter.queueId && !ids.has(filter.queueId)) ||
    p.has_more !== (p.next_cursor !== null) ||
    (p.has_more && p.items.length !== 25) ||
    (priorCursor && p.next_cursor === priorCursor) ||
    (watermark && timeKey(p.watermark) !== timeKey(watermark))
  )
    throw Error('Invalid queue basis');
  let previous = '';
  for (const row of p.items) {
    const key = timeKey(row.created_at) + ':' + row.id;
    if (
      seen.has(row.id) ||
      !ids.has(row.queue_id) ||
      (filter.queueId && row.queue_id !== filter.queueId) ||
      (filter.status && row.status !== filter.status) ||
      (filter.view === 'unassigned' && row.assigned_membership_id !== null) ||
      (filter.view === 'mine' && row.assigned_membership_id !== member.id) ||
      timeKey(row.created_at) > timeKey(p.watermark) ||
      (previous && key <= previous)
    )
      throw Error('Invalid queue item');
    seen.add(row.id);
    previous = key;
  }
  return p;
}
const capabilities = z
  .object({
    case_id: id,
    case_version: version,
    actor_principal_id: id,
    membership_id: id,
    membership_version: version,
    organization: label,
    queue: label,
    assignee: z
      .object({ membership_id: id, display_name: text.max(200).nullable() })
      .strict()
      .nullable(),
    active_conversation: z
      .object({ id, version, mode: z.enum(['waiting_staff', 'human']) })
      .strict()
      .nullable(),
    self_claim_membership_id: id.nullable(),
    allowed_actions: z.array(
      z.enum(['claim', 'assign', 'unassign', 'update_status', 'close']),
    ),
    allowed_statuses: z.array(
      z.enum(['in_progress', 'awaiting_customer', 'awaiting_external']),
    ),
    allowed_close_reasons: z.array(
      z.enum(['resolved', 'customer_cancelled', 'duplicate', 'no_response']),
    ),
  })
  .strict();
export type Capabilities = z.infer<typeof capabilities>;
export function decodeCapabilities(value: unknown) {
  return capabilities.parse(value);
}
