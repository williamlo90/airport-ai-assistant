import { z } from 'zod';
export const comment = z
  .string()
  .max(4000)
  .refine(
    (v) =>
      Array.from(v).length <= 2000 &&
      !v.includes('\0') &&
      !/[\uD800-\uDFFF]/u.test(v) &&
      (!v || !!v.trim()),
  );
export const ratingInput = z
  .object({
    score: z.number().int().min(1).max(5),
    comment: comment.nullable(),
    expected_version: z.number().int().min(0).max(2147483646),
  })
  .strict();
export const feedback = z.object({
  target_type: z.enum(['case', 'conversation']),
  target_id: z.uuid(),
  eligible: z.boolean(),
  rating: z
    .object({
      id: z.uuid(),
      score: z.number().int().min(1).max(5),
      comment: comment.nullable(),
      version: z.number().int().min(1).max(2147483647),
      created_at: z.iso.datetime({ offset: true }),
      updated_at: z.iso.datetime({ offset: true }),
    })
    .nullable(),
});
export type Feedback = z.infer<typeof feedback>;
export type RatingInput = z.infer<typeof ratingInput>;
export type Target = { kind: 'case' | 'conversation'; id: string };
