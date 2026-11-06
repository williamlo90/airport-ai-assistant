import { z } from 'zod';
const id = z.uuid(),
  language = z.enum(['en', 'id']),
  version = z.number().int().positive().max(Number.MAX_SAFE_INTEGER);
export const modes = ['ai', 'waiting_staff', 'human', 'ended'] as const;
const message = (author: 'customer' | 'staff' | 'ai') =>
  z
    .object({
      message_id: id,
      body: z
        .string()
        .min(1)
        .refine((v) => [...v].length <= 4000)
        .refine((v) => !!v.trim() && !v.includes('\0')),
      language,
      author_type: z.literal(author),
    })
    .strict();
const base = {
  id,
  sequence: version,
  created_at: z.string().datetime({ offset: true }),
};
const handoff = z
  .object({ case_id: id, mode: z.enum(['waiting_staff', 'human', 'ended']) })
  .strict();
const event = z.discriminatedUnion('event_type', [
  z
    .object({
      ...base,
      event_type: z.literal('message_received'),
      payload: message('customer'),
    })
    .strict(),
  z
    .object({
      ...base,
      event_type: z.literal('staff_message'),
      payload: message('staff'),
    })
    .strict(),
  z
    .object({
      ...base,
      event_type: z.literal('ai_message'),
      payload: message('ai'),
    })
    .strict(),
  z
    .object({
      ...base,
      event_type: z.literal('language_changed'),
      payload: z.object({ language }).strict(),
    })
    .strict(),
  ...(
    [
      'handoff_requested',
      'chat_accepted',
      'chat_reassigned',
      'chat_ended',
      'chat_updated',
    ] as const
  ).map((type) =>
    z
      .object({ ...base, event_type: z.literal(type), payload: handoff })
      .strict(),
  ),
]);
const page = z
  .object({
    items: z.array(event).max(50),
    next_cursor: z
      .string()
      .min(1)
      .max(2048)
      .regex(/^[A-Za-z0-9_-]+$/),
    has_more: z.boolean(),
    mode: z.enum(modes),
    generation_epoch: z
      .number()
      .int()
      .nonnegative()
      .max(Number.MAX_SAFE_INTEGER),
    version,
  })
  .strict();
export type ConversationEvent = z.infer<typeof event>;
export type EventPage = z.infer<typeof page>;
export type ConversationMode = EventPage['mode'];
export function decodeEvents(
  value: unknown,
  position: number,
  versionFloor: number,
  epochFloor: number,
  priorCursor: string | null,
) {
  const p = page.parse(value);
  if (
    p.version < versionFloor ||
    p.generation_epoch < epochFloor ||
    (p.has_more && p.items.length !== 50) ||
    (p.items.length && p.next_cursor === priorCursor)
  )
    throw Error('Invalid event snapshot');
  const ids = new Set<string>();
  for (const [index, item] of p.items.entries()) {
    if (item.sequence !== position + index + 1 || ids.has(item.id))
      throw Error('Invalid event order');
    ids.add(item.id);
    if (
      (item.event_type === 'handoff_requested' &&
        item.payload.mode !== 'waiting_staff') ||
      (item.event_type === 'chat_accepted' && item.payload.mode !== 'human') ||
      (item.event_type === 'chat_ended' && item.payload.mode !== 'ended')
    )
      throw Error('Invalid mode event');
  }
  return p;
}
