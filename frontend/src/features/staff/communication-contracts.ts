import { z } from 'zod';

const id = z.uuid();
const version = z.number().int().positive().max(Number.MAX_SAFE_INTEGER);
const text = z
  .string()
  .min(1)
  .refine((v) => [...v].length <= 4000 && !!v.trim() && !v.includes('\0'));
const language = z.enum(['en', 'id']);
const chat = z
  .object({
    conversation_id: id,
    case_id: id,
    case_version: version,
    version,
    generation_epoch: version,
    mode: z.enum(['waiting_staff', 'human', 'ended']),
    handler_membership_id: id.nullable(),
  })
  .strict();
const accepted = chat.extend({
  state: z.literal('accepted'),
  mode: z.literal('human'),
  handler_membership_id: id,
});
const saved = z
  .object({
    state: z.literal('saved'),
    message_id: id,
    conversation_id: id.nullable(),
    case_id: id,
    sequence: version,
    channel: z.enum(['chat', 'email']),
  })
  .strict()
  .refine((v) => v.conversation_id !== null || v.channel === 'email');
const translation = z
  .object({
    id,
    case_id: id,
    conversation_id: id.nullable(),
    original_body: text,
    source_language: language,
    target_language: language,
    channel: z.enum(['chat', 'email']),
    state: z.enum(['generating', 'ready', 'failed', 'sent']),
    translated_body: text.nullable(),
    reviewed_body: text.nullable(),
    message_id: id.nullable(),
    revision: version,
    expires_at: z.string().datetime({ offset: true }),
  })
  .strict()
  .refine(
    (v) =>
      (v.conversation_id !== null || v.channel === 'email') &&
      v.source_language !== v.target_language &&
      (v.state === 'sent') === (v.message_id !== null) &&
      (v.state === 'sent') === (v.reviewed_body !== null) &&
      (v.state === 'ready' || v.state === 'sent') ===
        (v.translated_body !== null),
  );

export type StaffChat = z.infer<typeof chat>;
export type StaffMessageReceipt = z.infer<typeof saved>;
export type StaffTranslation = z.infer<typeof translation>;

const capability = z
  .object({
    case_id: id,
    case_version: version,
    membership_id: id,
    email_available: z.boolean(),
  })
  .strict();
export type CommunicationCapability = z.infer<typeof capability>;
export function decodeCommunicationCapability(
  value: unknown,
  caseId: string,
  membershipId: string,
): CommunicationCapability {
  const result = capability.parse(value);
  if (result.case_id !== caseId || result.membership_id !== membershipId)
    throw Error('Mismatched communication scope');
  return result;
}

export function decodeStaffChat(
  value: unknown,
  caseId: string,
  conversationId: string,
): StaffChat {
  const result = chat.parse(value);
  if (
    result.case_id !== caseId ||
    result.conversation_id !== conversationId ||
    (result.mode === 'human' && result.handler_membership_id === null) ||
    (result.mode === 'waiting_staff' && result.handler_membership_id !== null)
  )
    throw Error('Mismatched conversation');
  return result;
}

export function decodeChatAcceptance(
  value: unknown,
  caseId: string,
  conversationId: string,
) {
  const result = accepted.parse(value);
  decodeStaffChat(resultWithoutState(result), caseId, conversationId);
  return result;
}
function resultWithoutState(value: z.infer<typeof accepted>) {
  const { state: _state, ...result } = value;
  void _state;
  return result;
}

export function decodeStaffReceipt(
  value: unknown,
  caseId: string,
  conversationId: string | null,
  channel: 'chat' | 'email',
): StaffMessageReceipt {
  const result = saved.parse(value);
  if (
    result.case_id !== caseId ||
    result.conversation_id !== conversationId ||
    result.channel !== channel
  )
    throw Error('Mismatched message receipt');
  return result;
}

export function decodeStaffTranslation(
  value: unknown,
  caseId: string,
  conversationId: string | null,
): StaffTranslation {
  const result = translation.parse(value);
  if (result.case_id !== caseId || result.conversation_id !== conversationId)
    throw Error('Mismatched translation');
  return result;
}

const correspondenceMessage = z
  .object({
    id,
    case_id: id,
    conversation_id: id.nullable(),
    author_type: z.enum(['customer', 'staff', 'ai']),
    body: z
      .string()
      .refine((v) => !!v.trim() && !v.includes('\0') && [...v].length <= 4000),
    language,
    sequence: version,
    channel: z.enum(['chat', 'email']),
    created_at: z.string().datetime({ offset: true }),
  })
  .strict()
  .refine(
    (v) =>
      v.author_type !== 'staff' ||
      v.conversation_id !== null ||
      v.channel === 'email',
  );
const correspondence = z
  .object({
    items: z.array(correspondenceMessage).max(50),
    has_more: z.boolean(),
    before_time: z.string().datetime({ offset: true }).nullable(),
    before_id: id.nullable(),
  })
  .strict();
export type StaffCorrespondencePage = z.infer<typeof correspondence>;
export function decodeStaffCorrespondence(
  value: unknown,
  caseId: string,
  limit = 50,
): StaffCorrespondencePage {
  const result = correspondence.parse(value);
  if (
    result.items.some((r) => r.case_id !== caseId) ||
    new Set(result.items.map((r) => r.id)).size !== result.items.length ||
    result.items.length > limit ||
    (result.has_more &&
      (result.items.length !== limit ||
        result.before_id !== result.items[0]?.id ||
        result.before_time !== result.items[0]?.created_at)) ||
    (!result.has_more &&
      (result.before_id !== null || result.before_time !== null))
  )
    throw Error('Invalid correspondence page');
  for (let i = 1; i < result.items.length; i++) {
    const previous = result.items[i - 1],
      current = result.items[i];
    if (!previous || !current) throw Error('Invalid message position');
    if (
      Date.parse(current.created_at) < Date.parse(previous.created_at) ||
      (current.created_at === previous.created_at && current.id <= previous.id)
    )
      throw Error('Invalid message order');
  }
  return result;
}
