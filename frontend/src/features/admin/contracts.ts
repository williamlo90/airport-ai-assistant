import { z } from 'zod';
export const roles = ['cs', 'supervisor', 'admin'] as const;
const unique = <T>(v: T[]) => new Set(v).size === v.length;
export const accessEdit = z
  .object({
    active: z.boolean(),
    roles: z.array(z.enum(roles)).min(1).max(3).refine(unique),
    queue_ids: z.array(z.uuid()).max(100).refine(unique),
  })
  .strict();
export const workspace = z.object({
  membership_id: z.uuid(),
  membership_version: z.number().int().positive().max(2147483647),
  organization: z.object({ id: z.uuid(), name: z.string().min(1) }),
  queues: z
    .array(
      z.object({ id: z.uuid(), name: z.string().min(1), active: z.boolean() }),
    )
    .refine((v) => unique(v.map((q) => q.id))),
});
export const member = z.object({
  membership_id: z.uuid(),
  principal_id: z.uuid(),
  organization_id: z.uuid(),
  display_name: z.string().nullable(),
  principal_active: z.boolean(),
  active: z.boolean(),
  version: z.number().int().positive().max(2147483647),
  roles: z.array(z.enum(roles)).min(1).max(3).refine(unique),
  queue_ids: z.array(z.uuid()).max(100).refine(unique),
});
export const result = z.object({ workspace, member });
export const directory = z
  .object({
    workspace,
    items: z.array(member).max(25),
    limit: z.literal(25),
    has_more: z.boolean(),
    next_after: z.uuid().nullable(),
    consistency: z.literal('live_keyset'),
  })
  .refine(
    (v) =>
      v.has_more === (v.next_after !== null) &&
      unique(v.items.map((m) => m.principal_id)) &&
      v.items.every((m) => m.organization_id === v.workspace.organization.id),
  );
export type AccessEdit = z.infer<typeof accessEdit>;
export type Workspace = z.infer<typeof workspace>;
export type Member = z.infer<typeof member>;
export type MemberResult = z.infer<typeof result>;
export type Directory = z.infer<typeof directory>;
