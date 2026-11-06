import { z } from 'zod';
import { record, uuid } from '../../api/contracts';
export interface Conversation {
  conversation_id: string;
  language: 'en' | 'id';
  version: number;
  generation_epoch: number;
  last_sequence: number;
}
const integer = (v: unknown, min: number): v is number =>
  typeof v === 'number' && Number.isSafeInteger(v) && v >= min;
export function decodeConversation(value: unknown): Conversation {
  const d = record(value);
  if (
    !uuid(d.conversation_id) ||
    (d.language !== 'en' && d.language !== 'id') ||
    !integer(d.version, 1) ||
    !integer(d.generation_epoch, 0) ||
    !integer(d.last_sequence, 0)
  )
    throw Error('Invalid conversation');
  return {
    conversation_id: d.conversation_id,
    language: d.language,
    version: d.version,
    generation_epoch: d.generation_epoch,
    last_sequence: d.last_sequence,
  };
}
export function decodeReceipt(value: unknown) {
  const d = record(value);
  if (
    !uuid(d.message_id) ||
    !integer(d.sequence, 1) ||
    d.processing_state !== 'received'
  )
    throw Error('Invalid receipt');
  return {
    message_id: d.message_id,
    sequence: d.sequence,
    processing_state: 'received' as const,
  };
}
export const messageSchema = z.object({
  body: z
    .string()
    .max(3800)
    .refine((v) => v.trim().length > 0 && !v.includes('\0')),
  service: z.enum(['', 'parking', 'lounge', 'other']),
});
export type MessageInput = z.infer<typeof messageSchema>;
