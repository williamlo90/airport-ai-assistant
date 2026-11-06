import { z } from 'zod';
import { record, uuid } from '../../api/contracts';
import { decodeDraft } from './contracts';
export const attachmentMetadata = z.object({
  id: z.uuid(),
  byte_size: z.number().int().min(1).max(5242880),
  mime_type: z.enum(['image/png', 'image/jpeg', 'application/pdf']),
  scan_status: z.literal('clean'),
});
export const decodeAttachmentList = (value: unknown) =>
  z
    .array(attachmentMetadata)
    .max(3)
    .refine(
      (items) => new Set(items.map((item) => item.id)).size === items.length,
    )
    .parse(value);
export const decodeUpload = (value: unknown) => {
  const row = record(value);
  return {
    attachment: attachmentMetadata.parse(row.attachment),
    draft: decodeDraft(row.draft),
  };
};
export const decodeAttachmentCapabilities = (value: unknown) =>
  z
    .object({
      upload_enabled: z.boolean(),
      max_bytes: z.literal(5242880),
      max_files: z.literal(3),
      media_types: z
        .array(z.enum(['image/png', 'image/jpeg', 'application/pdf']))
        .length(3)
        .refine((items) => new Set(items).size === 3),
    })
    .parse(value);
/** Matches uuid.uuid5(draft_id, str(request_uuid)); only the two opaque IDs are hashed. */
export async function attachmentId(draftId: string, requestKey: string) {
  if (!uuid(draftId) || !uuid(requestKey))
    throw new Error('Invalid upload IDs');
  const namespace = Uint8Array.from(
    draftId.replaceAll('-', '').match(/../g)!,
    (part) => Number.parseInt(part, 16),
  );
  const name = new TextEncoder().encode(requestKey.toLowerCase()),
    bytes = new Uint8Array(16 + name.length);
  bytes.set(namespace);
  bytes.set(name, 16);
  const hash = new Uint8Array(await crypto.subtle.digest('SHA-1', bytes)).slice(
    0,
    16,
  );
  hash[6] = (hash[6]! & 15) | 80;
  hash[8] = (hash[8]! & 63) | 128;
  const hex = Array.from(hash, (b) => b.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
