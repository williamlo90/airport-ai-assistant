import { z } from 'zod';

export const publicService = z.enum(['general', 'parking', 'lounge']);
export type PublicService = z.infer<typeof publicService>;
const publicAnswer = z
  .object({
    message_id: z.uuid(),
    request_key: z.uuid(),
    purpose: z.literal('answer'),
    service_type: publicService,
  })
  .strict();
export const privateReferences = z
  .object({
    booking_references: z
      .array(z.string().regex(/^[A-Za-z0-9_-]{1,80}$/))
      .max(1),
    transaction_references: z
      .array(z.string().regex(/^[A-Za-z0-9_-]{1,80}$/))
      .max(1),
  })
  .strict()
  .refine(
    (v) => v.booking_references.length + v.transaction_references.length > 0,
  );
export type PrivateReferences = z.infer<typeof privateReferences>;
const privateAssistance = z
  .object({
    message_id: z.uuid(),
    request_key: z.uuid(),
    purpose: z.literal('assist'),
    service_type: publicService,
    booking_references: z
      .array(z.string().regex(/^[A-Za-z0-9_-]{1,80}$/))
      .max(1),
    transaction_references: z
      .array(z.string().regex(/^[A-Za-z0-9_-]{1,80}$/))
      .max(1),
  })
  .strict()
  .refine(
    (v) => v.booking_references.length + v.transaction_references.length > 0,
  );
export const startAnswer = z.union([publicAnswer, privateAssistance]);
export type StartAnswer = z.infer<typeof startAnswer>;
const prepared = z
  .object({ run_id: z.uuid(), state: z.literal('prepared') })
  .strict();
const reason = z.string().min(1).max(200).optional();
const outcome = z.discriminatedUnion('state', [
  z
    .object({
      run_id: z.uuid(),
      state: z.literal('published'),
      message_id: z.uuid(),
      sequence: z.number().int().positive().max(Number.MAX_SAFE_INTEGER),
    })
    .strict(),
  z
    .object({ run_id: z.uuid(), state: z.literal('discarded'), reason })
    .strict(),
  z.object({ run_id: z.uuid(), state: z.literal('review'), reason }).strict(),
  z
    .object({ run_id: z.uuid(), state: z.literal('draft'), draft_id: z.uuid() })
    .strict(),
]);
export type AgentOutcome = z.infer<typeof outcome>;
const unresolved = z
  .object({ run_id: z.uuid(), state: z.literal('unresolved') })
  .strict();
export function decodeAgentStatus(value: unknown, runId: string) {
  const result = z.union([outcome, unresolved]).parse(value);
  if (result.run_id !== runId) throw Error('Invalid run receipt');
  return result;
}
export const decodePrepared = (value: unknown) => prepared.parse(value);
export function decodeAgentOutcome(
  value: unknown,
  runId: string,
): AgentOutcome {
  const result = outcome.parse(value);
  if (result.run_id !== runId) throw Error('Invalid run receipt');
  return result;
}
