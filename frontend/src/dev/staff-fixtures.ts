/** Synthetic preview/test data only. Never reads browser cookies or calls a network. */
import { AuthCoordinator } from '../app/auth/coordinator';
import { StaffStore } from '../features/staff/store';
export const STAFF = '11111111-1111-4111-8111-111111111111',
  MEMBER = '33333333-3333-4333-8333-333333333333',
  ORG = '44444444-4444-4444-8444-444444444444',
  QUEUE = '55555555-5555-4555-8555-555555555555',
  LOUNGE = '88888888-8888-4888-8888-888888888888';
export const caseId = (n: number) =>
  '90000000-0000-4000-8000-' + n.toString().padStart(12, '0');
const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
export const denied = (status = 404) =>
  json(
    {
      error: {
        code: 'RESOURCE_NOT_FOUND',
        message: 'Synthetic unavailable',
        request_id: STAFF,
        retryable: false,
      },
    },
    status,
  );
export function staffData(n: number) {
  const assigned = n % 4 === 0,
    id = caseId(n),
    created = '2026-11-01T09:00:00.' + n.toString().padStart(6, '0') + 'Z';
  return {
    id,
    public_reference: 'CASE-' + id.replaceAll('-', ''),
    status: assigned ? ('in_progress' as const) : ('new' as const),
    summary:
      n === 1
        ? 'Flight delay: parking booking ends tonight'
        : n === 2
          ? 'Lounge confirmation not found'
          : n % 2
            ? 'Help with an existing parking booking'
            : 'Question about a lounge reservation',
    assigned_membership_id: assigned ? MEMBER : null,
    next_actor: 'staff' as const,
    next_action: assigned
      ? 'Review the customer’s booking issue.'
      : 'A staff member needs to review this request.',
    version: assigned ? 2 : 1,
    updated_at: created,
    queue_id: n % 2 ? QUEUE : LOUNGE,
    created_at: created,
  };
}
export function staffDetail(n: number) {
  const row = staffData(n),
    created = {
      event_type: 'created',
      previous_version: 0,
      resulting_version: 1,
      from_status: null,
      to_status: null,
      next_actor: null,
      next_action: null,
      close_reason: null,
      resolution: null,
      customer_message: null,
      created_at: row.created_at,
    };
  const history: unknown[] = [created];
  if (row.assigned_membership_id)
    history.push({
      ...created,
      event_type: 'assigned',
      previous_version: 1,
      resulting_version: 2,
      from_status: 'new',
      to_status: 'in_progress',
      next_actor: 'staff',
      next_action: row.next_action,
    });
  return {
    ...row,
    category: 'booking_issue',
    service_type: n % 2 ? 'parking' : 'lounge',
    customer_report:
      n === 1
        ? 'My flight has been delayed until tomorrow morning. My parking booking ends tonight. Could you explain what I should do and who can help me request an extension?\n\nI would like to understand the procedure before changing anything.'
        : 'I have an existing reservation, but I need help understanding the next step. Please check the information I have provided and direct me to the right team.',
    booking_reference: n % 2 ? 'PK-WB-02418' : 'LG-WB-10942',
    transaction_reference: null,
    language: 'en',
    close_reason: null,
    resolution: null,
    related_case_ref: null,
    closed_at: null,
    history,
  };
}
export function makeStaffFixture() {
  type SyntheticDetail = Omit<
    ReturnType<typeof staffDetail>,
    | 'status'
    | 'assigned_membership_id'
    | 'next_actor'
    | 'close_reason'
    | 'resolution'
    | 'closed_at'
  > & {
    status:
      | 'new'
      | 'in_progress'
      | 'awaiting_customer'
      | 'awaiting_external'
      | 'closed';
    assigned_membership_id: string | null;
    next_actor: 'staff' | 'customer' | 'external' | 'none';
    close_reason:
      'resolved' | 'customer_cancelled' | 'duplicate' | 'no_response' | null;
    resolution: string | null;
    closed_at: string | null;
  };
  const records = new Map<number, SyntheticDetail>();
  const record = (n: number): SyntheticDetail =>
    records.get(n) ?? staffDetail(n);
  const server = {
    principal: STAFF,
    proof: 's'.repeat(43),
    active: true,
    memberVersion: 1,
    roles: ['cs'] as ('cs' | 'supervisor' | 'admin')[],
    queueIds: [QUEUE, LOUNGE],
    count: 60,
    requests: [] as { path: string; method: string }[],
    custom: null as
      null | ((path: string, init?: RequestInit) => Promise<Response> | null),
  };
  const fetcher: typeof fetch = async (input, init) => {
    const path = String(input);
    server.requests.push({ path, method: init?.method ?? 'GET' });
    const custom = server.custom?.(path, init);
    if (custom) return custom;
    if (path === '/api/v1/me')
      return server.active
        ? json({ session_kind: 'staff', principal_id: server.principal })
        : denied(401);
    if (path === '/api/v1/staff/me')
      return server.active
        ? json({
            session_kind: 'staff',
            principal_id: server.principal,
            memberships: [
              {
                id: MEMBER,
                organization_id: ORG,
                version: server.memberVersion,
                roles: server.roles,
                queue_ids: server.queueIds,
              },
            ],
          })
        : denied(401);
    if (!server.active || server.roles.every((r) => r === 'admin'))
      return denied();
    const url = new URL(path, 'https://preview.invalid'),
      params = url.searchParams;
    if (
      (init?.method ?? 'GET') === 'GET' &&
      params.get('membership_id') !== MEMBER
    )
      return denied();
    const operation =
      /^\/api\/v1\/staff\/cases\/(90000000-0000-4000-8000-\d{12})\/(assignment|status|eligible-assignees|duplicate-candidates\/search)$/.exec(
        url.pathname,
      );
    if (operation) {
      const n = Number(operation[1]!.slice(-12)),
        current = record(n),
        supervisor = server.roles.includes('supervisor');
      if (
        n < 1 ||
        n > server.count ||
        !server.queueIds.includes(current.queue_id)
      )
        return denied();
      if (operation[2] === 'duplicate-candidates/search') {
        const body = JSON.parse(String(init?.body));
        if (body.membership_id !== MEMBER) return denied();
        if (
          body.expected_version !== current.version ||
          current.status === 'closed'
        )
          return denied(409);
        const candidate = Array.from({ length: server.count }, (_, i) =>
          record(i + 1),
        ).find(
          (v) =>
            v.id !== current.id &&
            v.public_reference === body.public_reference &&
            server.queueIds.includes(v.queue_id),
        );
        return json({
          case_id: current.id,
          case_version: current.version,
          membership_id: MEMBER,
          membership_version: server.memberVersion,
          items: candidate
            ? [
                {
                  case_id: candidate.id,
                  public_reference: candidate.public_reference,
                },
              ]
            : [],
        });
      }
      if (operation[2] === 'eligible-assignees')
        return supervisor
          ? json({
              case_id: current.id,
              case_version: current.version,
              membership_id: MEMBER,
              membership_version: server.memberVersion,
              items: [
                { membership_id: MEMBER, display_name: 'Taylor Morgan' },
                {
                  membership_id: '77777777-7777-4777-8777-777777777777',
                  display_name: 'Jordan Lee',
                },
              ],
              has_more: false,
              next_cursor: null,
            })
          : denied(403);
      if (init?.method !== 'PATCH') return denied();
      const body = JSON.parse(String(init.body)) as {
        expected_version: number;
        assignee_id?: string | null;
        status?: SyntheticDetail['status'];
        next_action?: string;
        close_reason?: SyntheticDetail['close_reason'];
        resolution?: string;
        related_case_id?: string;
        followup_evidence?: string;
      };
      if (
        body.expected_version !== current.version ||
        current.status === 'closed'
      )
        return denied(409);
      const claim = operation[2] === 'assignment' && !supervisor;
      if (
        claim &&
        (current.assigned_membership_id !== null || body.assignee_id !== MEMBER)
      )
        return denied(403);
      const next: SyntheticDetail = {
        ...current,
        version: current.version + 1,
        updated_at: '2026-11-04T01:01:00Z',
        history: [...current.history],
      };
      if (operation[2] === 'assignment') {
        next.assigned_membership_id = body.assignee_id ?? null;
        if (claim) {
          next.status = 'in_progress';
          next.next_action = 'Staff is handling this case.';
        }
      } else {
        next.status = body.status!;
        next.next_action = body.next_action!;
        next.next_actor = (
          body.status === 'awaiting_customer'
            ? 'customer'
            : body.status === 'awaiting_external'
              ? 'external'
              : 'staff'
        ) as typeof next.next_actor;
      }
      if (next.status === 'closed') {
        next.next_actor = 'none';
        next.close_reason = body.close_reason!;
        next.resolution = body.resolution!;
        next.closed_at = next.updated_at;
      }
      next.history.push({
        event_type:
          operation[2] === 'assignment' ? 'assigned' : 'status_changed',
        previous_version: current.version,
        resulting_version: next.version,
        from_status: current.status,
        to_status: next.status,
        next_actor: next.next_actor,
        next_action: next.next_action,
        close_reason: next.close_reason,
        resolution: next.resolution,
        customer_message: null,
        created_at: next.updated_at,
      });
      records.set(n, next);
      const { queue_id: unusedQueue, created_at: unusedCreated, ...dto } = next;
      void unusedQueue;
      void unusedCreated;
      return json(dto);
    }
    if (url.pathname === '/api/v1/staff/queues/cases') {
      const view = params.get('view') ?? 'all',
        status = params.get('status'),
        queue = params.get('queue_id'),
        limit = Number(params.get('limit') ?? 25),
        offset = Number(params.get('cursor')?.slice(1) ?? 0);
      const rows = Array.from({ length: server.count }, (_, i) =>
        (() => {
          const d = record(i + 1);
          return {
            ...staffData(i + 1),
            status: d.status,
            version: d.version,
            assigned_membership_id: d.assigned_membership_id,
            next_actor: d.next_actor,
            next_action: d.next_action,
            updated_at: d.updated_at,
          };
        })(),
      ).filter(
        (v) =>
          server.queueIds.includes(v.queue_id) &&
          (!queue || v.queue_id === queue) &&
          (!status || v.status === status) &&
          (view === 'all' ||
            (view === 'mine'
              ? v.assigned_membership_id === MEMBER
              : v.assigned_membership_id === null)),
      );
      const items = rows.slice(offset, offset + limit),
        more = offset + limit < rows.length;
      return json({
        membership_id: MEMBER,
        membership_version: server.memberVersion,
        organization: { id: ORG, name: 'Westbridge Passenger Services' },
        queues: server.queueIds.map((id) => ({
          id,
          name: id === QUEUE ? 'Parking support' : 'Lounge support',
        })),
        queue_id: queue,
        view,
        status,
        sort: 'created_asc',
        limit,
        items,
        has_more: more,
        next_cursor: more ? 'p' + (offset + limit) : null,
        consistency: 'live_keyset',
        watermark: '2026-11-04T00:00:00Z',
        observed_at: '2026-11-04T01:00:00Z',
      });
    }
    const match =
      /^\/api\/v1\/staff\/cases\/(90000000-0000-4000-8000-\d{12})(\/capabilities)?$/.exec(
        url.pathname,
      );
    if (match) {
      const n = Number(match[1]!.slice(-12));
      if (n < 1 || n > server.count) return denied();
      const detail = record(n),
        supervisor = server.roles.includes('supervisor');
      if (!server.queueIds.includes(detail.queue_id)) return denied();
      if (match[2])
        return json({
          case_id: detail.id,
          case_version: detail.version,
          actor_principal_id: server.principal,
          membership_id: MEMBER,
          membership_version: server.memberVersion,
          organization: { id: ORG, name: 'Westbridge Passenger Services' },
          queue: {
            id: detail.queue_id,
            name:
              detail.queue_id === QUEUE ? 'Parking support' : 'Lounge support',
          },
          assignee: detail.assigned_membership_id
            ? {
                membership_id: detail.assigned_membership_id,
                display_name:
                  detail.assigned_membership_id === MEMBER
                    ? 'Taylor Morgan'
                    : 'Jordan Lee',
              }
            : null,
          active_conversation: null,
          self_claim_membership_id:
            detail.assigned_membership_id || supervisor ? null : MEMBER,
          allowed_actions:
            detail.status === 'closed'
              ? []
              : [
                  ...(supervisor
                    ? [
                        'assign',
                        ...(detail.status === 'new' &&
                        detail.assigned_membership_id
                          ? ['unassign']
                          : []),
                      ]
                    : detail.assigned_membership_id
                      ? []
                      : ['claim']),
                  ...(detail.assigned_membership_id ? ['update_status'] : []),
                  'close',
                ],
          allowed_statuses: detail.assigned_membership_id
            ? detail.status === 'new'
              ? ['in_progress']
              : detail.status === 'awaiting_customer'
                ? ['awaiting_customer', 'in_progress']
                : detail.status === 'awaiting_external'
                  ? ['awaiting_external', 'in_progress']
                  : ['in_progress', 'awaiting_customer', 'awaiting_external']
            : [],
          allowed_close_reasons: [
            'resolved',
            'customer_cancelled',
            'duplicate',
            'no_response',
          ],
        });
      const {
        queue_id: unusedQueue,
        created_at: unusedCreated,
        ...dto
      } = detail;
      void unusedQueue;
      void unusedCreated;
      return json(dto);
    }
    return denied();
  };
  const auth = new AuthCoordinator({
      fetch: fetcher,
      readProof: () => server.proof,
    }),
    staff = new StaffStore(auth),
    off = staff.connect();
  return {
    server,
    auth,
    staff,
    fetcher,
    json,
    async ready() {
      await auth.start();
    },
    dispose() {
      off();
      staff.dispose();
      auth.dispose();
    },
  };
}
