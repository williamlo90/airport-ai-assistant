import { z } from 'zod';

/** Mirrors knowledge source metadata: managed URNs or credential-free HTTPS. */
export function citationSource(value: string): 'https' | 'urn' | null {
  if (
    !value ||
    value.length > 2048 ||
    /[\s\\]/u.test(value) ||
    [...value].some((c) => c.charCodeAt(0) < 33 || c.charCodeAt(0) === 127)
  )
    return null;
  if (/^urn:airport:[A-Za-z0-9][A-Za-z0-9:._/-]*$/.test(value)) return 'urn';
  if (!/^https:\/\//i.test(value) || /[?#]/.test(value)) return null;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' &&
      url.hostname &&
      !url.username &&
      !url.password
      ? 'https'
      : null;
  } catch {
    return null;
  }
}

const citation = z
  .object({
    version_id: z.uuid(),
    generation: z.uuid(),
    ordinal: z.number().int().min(0).max(62),
    basis_hash: z.string().regex(/^[a-f0-9]{64}$/),
    document_id: z.uuid(),
    document_revision: z.number().int().positive().max(Number.MAX_SAFE_INTEGER),
    quote: z
      .string()
      .refine((v) => !!v.trim() && !v.includes('\0') && [...v].length <= 384),
    source_uri: z.string().refine((v) => citationSource(v) !== null),
  })
  .strict();
const receipt = z
  .object({
    message_id: z.uuid(),
    status: z.literal('historical_publication'),
    issued_at: z.string().datetime({ offset: true }).nullable(),
    citations: z.array(citation).max(3),
  })
  .strict();
export type CitationReceipt = z.infer<typeof receipt>;

export function decodeCitations(
  value: unknown,
  messageId: string,
): CitationReceipt {
  const result = receipt.parse(value);
  const keys = result.citations.map(
    (c) => `${c.version_id}:${c.generation}:${c.ordinal}`,
  );
  if (
    result.message_id !== messageId ||
    (result.citations.length > 0 && result.issued_at === null) ||
    new Set(keys).size !== keys.length
  )
    throw Error('Invalid citation receipt');
  return result;
}
