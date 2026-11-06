import { z } from 'zod';
const id = z.uuid(),
  versionNumber = z.number().int().positive().max(2147483647),
  date = z.iso.datetime({ offset: true }),
  hash = z.string().regex(/^[a-f0-9]{64}$/);
const text = (max: number) =>
  z
    .string()
    .min(1)
    .refine(
      (v) =>
        Array.from(v).length <= max &&
        !!v.trim() &&
        !v.includes('\0') &&
        !/[\uD800-\uDFFF]/u.test(v),
    );
export const key = z
  .string()
  .min(1)
  .max(100)
  .regex(/^[a-z0-9]+(?:-[a-z0-9]+)*$/);
export const content = z
  .object({
    body: text(20000),
    source_uri: text(2048).refine((v) => {
      if (v.startsWith('urn:airport:'))
        return /^urn:airport:[A-Za-z0-9][A-Za-z0-9:._/-]*$/.test(v);
      try {
        const u = new URL(v);
        return (
          u.protocol === 'https:' &&
          !u.username &&
          !u.password &&
          !v.includes('?') &&
          !v.includes('#') &&
          !/\s/u.test(v)
        );
      } catch {
        return false;
      }
    }),
    service_type: z.enum(['general', 'parking', 'lounge']),
    language: z.enum(['en', 'id']),
    visibility: z.enum(['public', 'staff']),
    allowed_roles: z.array(z.enum(['admin', 'cs', 'supervisor'])).max(3),
    valid_from: date,
    valid_to: date.nullable(),
    applicability: z.object({
      terminal: z
        .string()
        .min(1)
        .max(32)
        .regex(/^[A-Za-z0-9_-]+$/)
        .nullable(),
      booking_created_from: date.nullable(),
      booking_created_to: date.nullable(),
    }),
    policy_reference: text(200).nullable(),
    supersedes_version_id: id.nullable(),
  })
  .superRefine((v, c) => {
    if (
      (v.visibility === 'staff') !== !!v.allowed_roles.length ||
      new Set(v.allowed_roles).size !== v.allowed_roles.length ||
      (v.valid_to && Date.parse(v.valid_to) <= Date.parse(v.valid_from)) ||
      (v.applicability.booking_created_from &&
        v.applicability.booking_created_to &&
        Date.parse(v.applicability.booking_created_to) <=
          Date.parse(v.applicability.booking_created_from))
    )
      c.addIssue({
        code: 'custom',
        message: 'Check visibility and effective dates',
      });
  });
export const document = z.object({
  id,
  organization_id: id,
  document_key: key,
  title: text(200),
  revision: versionNumber,
  created_by: id,
  created_at: date,
  updated_at: date,
});
export const version = content.safeExtend({
  id,
  document_id: id,
  version: versionNumber,
  revision: versionNumber,
  organization_id: id.nullable(),
  content_hash: hash,
  basis_hash: hash,
  status: z.enum(['draft', 'published', 'withdrawn']),
  published_at: date.nullable(),
  published_by: id.nullable(),
  withdrawn_at: date.nullable(),
  withdrawn_by: id.nullable(),
  withdrawal_reason: z.string().nullable(),
  index_generation: id.nullable(),
  created_by: id,
  created_at: date,
  updated_at: date,
});
export const directory = z.object({
  membership_id: id,
  organization_id: id,
  items: z.array(document).max(25),
  limit: z.literal(25),
  has_more: z.boolean(),
  next_after: id.nullable(),
  consistency: z.literal('live_keyset'),
});
export const versions = z.object({
  items: z.array(version).max(20),
  has_more: z.boolean(),
  next_after: z.number().int().positive().nullable(),
});
export const indexProof = z.object({
  version_id: id,
  generation: id,
  basis_hash: hash,
  valid_until: date,
});
export type Content = z.infer<typeof content>;
export type Document = z.infer<typeof document>;
export type Version = z.infer<typeof version>;
export type Directory = z.infer<typeof directory>;
export type Versions = z.infer<typeof versions>;
export function toContent(v: Version): Content {
  return content.parse(v);
}

export const indexStatus = z
  .object({
    version_id: id,
    basis_hash: hash,
    ready: z.boolean(),
    generation: id.nullable(),
    indexing_available: z.boolean(),
  })
  .refine((v) => v.ready === (v.generation !== null));
export type IndexStatus = z.infer<typeof indexStatus>;
