import { decodeSession, record, uuid } from '../../api/contracts';
import type { Decoder } from '../../api/contracts';
export interface Membership {
  readonly id: string;
  readonly organization_id: string;
  readonly version: number;
  readonly roles: readonly ('cs' | 'supervisor' | 'admin')[];
  readonly queue_ids: readonly string[];
}
export interface StaffView {
  readonly session_kind: 'staff';
  readonly principal_id: string;
  readonly memberships: readonly Membership[];
}
export const decodeStaff: Decoder<StaffView> = (value) => {
  const data = record(value),
    session = decodeSession(value);
  if (
    session.session_kind !== 'staff' ||
    !session.principal_id ||
    !Array.isArray(data.memberships) ||
    !data.memberships.length ||
    data.memberships.length > 1000
  )
    throw new Error('Invalid staff DTO');
  const seen = new Set<string>();
  const memberships = data.memberships.map((raw) => {
    const m = record(raw);
    if (
      !uuid(m.id) ||
      seen.has(m.id) ||
      !uuid(m.organization_id) ||
      !Number.isSafeInteger(m.version) ||
      (m.version as number) < 1 ||
      !Array.isArray(m.roles) ||
      !m.roles.length ||
      m.roles.some((r) => !['cs', 'supervisor', 'admin'].includes(r)) ||
      !Array.isArray(m.queue_ids) ||
      m.queue_ids.some((q) => !uuid(q))
    )
      throw new Error('Invalid membership DTO');
    seen.add(m.id);
    return Object.freeze({
      id: m.id,
      organization_id: m.organization_id,
      version: m.version as number,
      roles: Object.freeze([...new Set(m.roles)]) as Membership['roles'],
      queue_ids: Object.freeze([...new Set(m.queue_ids)]) as readonly string[],
    });
  });
  return Object.freeze({
    session_kind: 'staff',
    principal_id: session.principal_id,
    memberships: Object.freeze(memberships),
  });
};
export type AuthSignal = {
  readonly type: 'auth_transition_begin' | 'auth_transition_end';
  readonly transitionId: string;
};
export interface SignalPort {
  publish(signal: AuthSignal): void;
  subscribe(receive: (signal: unknown) => void): () => void;
}
export function parseSignal(value: unknown): AuthSignal | null {
  try {
    const v = record(value);
    if (
      Object.keys(v).length !== 2 ||
      !['auth_transition_begin', 'auth_transition_end'].includes(
        String(v.type),
      ) ||
      !uuid(v.transitionId)
    )
      return null;
    return { type: v.type as AuthSignal['type'], transitionId: v.transitionId };
  } catch {
    return null;
  }
}
export function readCsrfCookie(cookie: string): string | null {
  const values = cookie
    .split(';')
    .map((v) => v.trim())
    .filter((v) => v.startsWith('__Host-airport_csrf='));
  if (values.length !== 1) return null;
  const proof = values[0]!.slice('__Host-airport_csrf='.length);
  return /^[a-zA-Z0-9_-]{43}$/.test(proof) ? proof : null;
}
