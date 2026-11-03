/** Ports owned by the future auth coordinator, independent of React. */
export interface BoundarySnapshot {
  readonly boundaryId: string;
  readonly authEpoch: number;
  readonly sessionKind: 'anonymous' | 'customer' | 'staff';
  readonly principalId: string | null;
  readonly csrfProof: string;
}
export interface BoundaryPort {
  isCurrent(snapshot: BoundarySnapshot): boolean;
  readCsrfProof(): string | null;
  invalidate(reason: 'unauthorized' | 'boundary-changed'): void;
}
export type Decoder<T> = (value: unknown) => T;
export function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value))
    throw new Error('Invalid DTO');
  return value as Record<string, unknown>;
}
export function uuid(value: unknown): value is string {
  return (
    typeof value === 'string' &&
    /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i.test(value)
  );
}
export interface SessionView {
  readonly session_kind: BoundarySnapshot['sessionKind'];
  readonly principal_id: string | null;
}
export const decodeSession: Decoder<SessionView> = (value) => {
  const data = record(value);
  if (
    typeof data.session_kind !== 'string' ||
    !['anonymous', 'customer', 'staff'].includes(data.session_kind) ||
    (data.session_kind === 'anonymous'
      ? data.principal_id !== null
      : !uuid(data.principal_id))
  )
    throw new Error('Invalid session DTO');
  return {
    session_kind: data.session_kind as SessionView['session_kind'],
    principal_id: data.principal_id as string | null,
  };
};
export const decodeChallenge: Decoder<{
  challenge_id: string;
  status: 'verification_requested';
}> = (value) => {
  const data = record(value);
  if (!uuid(data.challenge_id) || data.status !== 'verification_requested')
    throw new Error('Invalid challenge DTO');
  return { challenge_id: data.challenge_id, status: 'verification_requested' };
};
export const decodeEmpty: Decoder<void> = (value) => {
  if (value !== undefined) throw new Error('Expected empty response');
};
