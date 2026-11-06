import { z } from 'zod';
import type { Capabilities } from './queue-contracts';
import { decodeCase, type CaseDetail } from '../cases/detail-contracts';
export type WorkStatus =
  'in_progress' | 'awaiting_customer' | 'awaiting_external';
export type CloseReason =
  'resolved' | 'customer_cancelled' | 'duplicate' | 'no_response';
export type Candidate = { case_id: string; public_reference: string };
export type StaffCommand =
  | { kind: 'assignment'; target: string | null }
  | { kind: 'status'; status: WorkStatus; nextAction: string }
  | {
      kind: 'close';
      reason: CloseReason;
      resolution: string;
      nextAction: string;
      candidate: Candidate | null;
      evidence: string;
    };
const label = z
  .object({
    membership_id: z.uuid(),
    display_name: z.string().min(1).max(200).nullable(),
  })
  .strict();
const page = z
  .object({
    case_id: z.uuid(),
    case_version: z.number().int().positive(),
    membership_id: z.uuid(),
    membership_version: z.number().int().positive(),
    items: z.array(label).max(25),
    has_more: z.boolean(),
    next_cursor: z
      .string()
      .min(1)
      .max(2048)
      .regex(/^[A-Za-z0-9_-]+$/)
      .nullable(),
  })
  .strict();
export type AssigneePage = z.infer<typeof page>;
export function decodeAssignees(
  value: unknown,
  cap: Capabilities,
  cursor: string | null,
) {
  const p = page.parse(value);
  if (
    p.case_id !== cap.case_id ||
    p.case_version !== cap.case_version ||
    p.membership_id !== cap.membership_id ||
    p.membership_version !== cap.membership_version ||
    p.has_more !== (p.next_cursor !== null) ||
    (p.has_more && p.items.length !== 25) ||
    (cursor && p.next_cursor === cursor)
  )
    throw Error('Invalid assignee basis');
  let last = '';
  for (const item of p.items) {
    if (
      item.membership_id <= last ||
      (item.display_name !== null &&
        (!item.display_name.trim() || item.display_name.includes('\0')))
    )
      throw Error('Invalid assignee page');
    last = item.membership_id;
  }
  return p;
}
export function decodeMutation(
  value: unknown,
  before: CaseDetail,
  command: StaffCommand,
  claim: boolean,
) {
  const result = decodeCase(value);
  const event = result.history.at(-1);
  if (
    result.id !== before.id ||
    result.version !== before.version + 1 ||
    result.public_reference !== before.public_reference ||
    !event ||
    event.resulting_version !== result.version ||
    event.from_status !== before.status ||
    event.to_status !== result.status ||
    event.event_type !==
      (command.kind === 'assignment' ? 'assigned' : 'status_changed') ||
    result.next_actor !==
      (result.status === 'closed'
        ? 'none'
        : result.status === 'awaiting_customer'
          ? 'customer'
          : result.status === 'awaiting_external'
            ? 'external'
            : 'staff') ||
    (command.kind === 'assignment'
      ? result.assigned_membership_id !== command.target ||
        result.status !== (claim ? 'in_progress' : before.status)
      : command.kind === 'close'
        ? result.status !== 'closed' ||
          result.close_reason !== command.reason ||
          result.resolution !== command.resolution ||
          result.next_action !== command.nextAction ||
          result.assigned_membership_id !== before.assigned_membership_id ||
          event.next_actor !== 'none' ||
          event.next_action !== command.nextAction ||
          event.close_reason !== command.reason ||
          event.resolution !== command.resolution ||
          (command.reason === 'duplicate' &&
            result.related_case_ref != null &&
            result.related_case_ref !== command.candidate?.public_reference)
        : result.status !== command.status ||
          result.next_action !== command.nextAction ||
          result.assigned_membership_id !== before.assigned_membership_id)
  )
    throw Error('Invalid mutation receipt');
  return result;
}

const candidates = z
  .object({
    case_id: z.uuid(),
    case_version: z.number().int().positive(),
    membership_id: z.uuid(),
    membership_version: z.number().int().positive(),
    items: z
      .array(
        z
          .object({
            case_id: z.uuid(),
            public_reference: z.string().regex(/^CASE-[a-f0-9]{32}$/),
          })
          .strict(),
      )
      .max(1),
  })
  .strict();
export function decodeCandidates(
  value: unknown,
  cap: Capabilities,
  reference: string,
) {
  const p = candidates.parse(value);
  if (
    p.case_id !== cap.case_id ||
    p.case_version !== cap.case_version ||
    p.membership_id !== cap.membership_id ||
    p.membership_version !== cap.membership_version ||
    p.items.some(
      (v) => v.case_id === cap.case_id || v.public_reference !== reference,
    )
  )
    throw Error('Invalid candidate basis');
  return p.items[0] ?? null;
}
