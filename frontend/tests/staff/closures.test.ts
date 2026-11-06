import { afterEach, expect, it } from 'vitest';
import {
  makeStaffFixture,
  caseId,
  MEMBER,
  denied,
} from '../../src/dev/staff-fixtures';
import { StaffQueueStore } from '../../src/features/staff/queue-store';
import { StaffActionStore } from '../../src/features/staff/action-store';
import { decodeCandidates } from '../../src/features/staff/action-contracts';
const clean: (() => void)[] = [];
async function setup(n = 4) {
  const f = makeStaffFixture();
  await f.ready();
  const q = new StaffQueueStore(f.staff, 'test'),
    off = q.connect();
  await q.ensureQueue();
  await q.openCase(caseId(n));
  const a = new StaffActionStore(q, caseId(n)),
    offa = a.connect();
  clean.push(() => {
    offa();
    a.dispose();
    off();
    q.dispose();
    f.dispose();
  });
  a.edit({
    closeReason: 'resolved',
    resolution: 'The parking team confirmed the extension.',
    closeNext: 'No further action needed.',
  });
  return { ...f, q, a };
}
afterEach(() => clean.splice(0).forEach((f) => f()));
it.each(['resolved', 'customer_cancelled', 'no_response'] as const)(
  'confirms %s once with exact typed status payload and fresh closed read',
  async (reason) => {
    const f = await setup();
    f.a.edit({
      closeReason: reason,
      evidence:
        reason === 'no_response'
          ? 'Followed up by email on 4 November, reference WB-42.'
          : '',
    });
    let body: unknown;
    f.server.custom = (path, init) => {
      if (init?.method === 'PATCH') {
        expect(path.endsWith('/status')).toBe(true);
        body = JSON.parse(String(init.body));
      }
      return null;
    };
    f.a.requestClose();
    expect(f.a.getState().closeConfirmation).toBe(true);
    expect(f.server.requests.some((r) => r.method === 'PATCH')).toBe(false);
    await Promise.all([f.a.confirmClose(), f.a.confirmClose()]);
    expect(body).toEqual({
      expected_version: 2,
      status: 'closed',
      close_reason: reason,
      resolution: 'The parking team confirmed the extension.',
      next_action: 'No further action needed.',
      ...(reason === 'no_response'
        ? {
            followup_evidence:
              'Followed up by email on 4 November, reference WB-42.',
          }
        : {}),
    });
    expect(f.server.requests.filter((r) => r.method === 'PATCH')).toHaveLength(
      1,
    );
    expect(f.q.getState().detail).toMatchObject({
      status: 'closed',
      next_actor: 'none',
      version: 3,
      close_reason: reason,
    });
    expect(f.a.getState().notice).toBe('saved');
    expect(f.a.dirty()).toBe(false);
  },
);
it('cancellation and edits invalidate confirmation without mutations', async () => {
  const f = await setup();
  f.a.requestClose();
  f.a.cancelClose();
  await f.a.confirmClose();
  f.a.requestClose();
  f.a.edit({ resolution: 'Changed outcome' });
  await f.a.confirmClose();
  expect(f.server.requests.some((r) => r.method === 'PATCH')).toBe(false);
});
it('requires evidence, nonblank texts, max limits and allowed reason', async () => {
  const f = await setup();
  for (const edit of [
    { resolution: ' ' },
    { resolution: 'x'.repeat(4001) },
    { resolution: 'abc\0' },
    { resolution: 'Valid', closeNext: ' ' },
    { closeNext: 'Valid', closeReason: 'no_response' as const, evidence: '' },
  ]) {
    f.a.edit(edit);
    f.a.requestClose();
    expect(f.a.getState().closeConfirmation).toBe(false);
    expect(f.a.getState().notice).toBe('invalid-close');
  }
  expect(f.server.requests.some((r) => r.method === 'PATCH')).toBe(false);
});
it('exact lookup binds minimal candidate and supplies related_case_id only for duplicate', async () => {
  const f = await setup();
  const reference = 'CASE-' + caseId(2).replaceAll('-', '');
  f.a.edit({ closeReason: 'duplicate', reference });
  f.a.requestClose();
  expect(f.a.getState().closeConfirmation).toBe(false);
  await f.a.findDuplicate();
  expect(f.a.getState().candidate).toEqual({
    case_id: caseId(2),
    public_reference: reference,
  });
  const lookup = f.server.requests.find((r) => r.method === 'POST');
  expect(lookup?.path).toContain('/duplicate-candidates/search');
  let body: Record<string, unknown> = {};
  f.server.custom = (_, init) => {
    if (init?.method === 'PATCH') body = JSON.parse(String(init.body));
    return null;
  };
  f.a.requestClose();
  await f.a.confirmClose();
  expect(body.related_case_id).toBe(caseId(2));
  expect(body.followup_evidence).toBeUndefined();
  expect(f.a.getState().notice).toBe('saved');
});
it('generic empty result and invalid reference cannot close', async () => {
  const f = await setup();
  f.a.edit({ closeReason: 'duplicate', reference: 'CASE-partial' });
  await f.a.findDuplicate();
  expect(f.server.requests.some((r) => r.method === 'POST')).toBe(false);
  f.a.edit({ reference: 'CASE-' + 'f'.repeat(32) });
  await f.a.findDuplicate();
  expect(f.a.getState().lookup).toBe('empty');
  f.a.requestClose();
  await f.a.confirmClose();
  expect(f.server.requests.some((r) => r.method === 'PATCH')).toBe(false);
});
it('candidate disappears when reference or reason changes; stale version invalidates confirmation', async () => {
  const f = await setup();
  f.a.edit({
    closeReason: 'duplicate',
    reference: 'CASE-' + caseId(2).replaceAll('-', ''),
  });
  await f.a.findDuplicate();
  f.a.edit({ reference: 'CASE-' + caseId(3).replaceAll('-', '') });
  expect(f.a.getState().candidate).toBeNull();
  await f.a.findDuplicate();
  f.a.edit({ closeReason: 'resolved' });
  expect(f.a.getState().candidate).toBeNull();
  f.a.requestClose();
  await f.q.openCase(caseId(1));
  await f.a.confirmClose();
  expect(f.server.requests.some((r) => r.method === 'PATCH')).toBe(false);
});
it('409 keeps authorized text but drops duplicate lookup/confirmation and never retries automatically', async () => {
  const f = await setup();
  f.a.edit({
    closeReason: 'duplicate',
    reference: 'CASE-' + caseId(2).replaceAll('-', ''),
  });
  await f.a.findDuplicate();
  f.server.custom = (_, init) =>
    init?.method === 'PATCH' ? Promise.resolve(denied(409)) : null;
  f.a.requestClose();
  await f.a.confirmClose();
  expect(f.a.getState()).toMatchObject({
    phase: 'review',
    resolution: 'The parking team confirmed the extension.',
    candidate: null,
    closeConfirmation: false,
  });
  await f.a.confirmClose();
  expect(f.server.requests.filter((r) => r.method === 'PATCH')).toHaveLength(1);
  f.a.review();
  f.a.requestClose();
  expect(f.a.getState().closeConfirmation).toBe(false);
});
it('unknown outcome uses reads only and cannot infer own success', async () => {
  const f = await setup();
  f.server.custom = (_, init) =>
    init?.method === 'PATCH' ? Promise.reject(new TypeError('lost')) : null;
  f.a.requestClose();
  await f.a.confirmClose();
  expect(f.a.getState()).toMatchObject({
    notice: 'uncertain',
    phase: 'review',
  });
  await f.a.confirmClose();
  expect(f.server.requests.filter((r) => r.method === 'PATCH')).toHaveLength(1);
});
it('403 hides all close input and restores draft only after same-proof scoped reads', async () => {
  const f = await setup();
  f.server.custom = (_, init) =>
    init?.method === 'PATCH' ? Promise.resolve(denied(403)) : null;
  f.a.requestClose();
  await f.a.confirmClose();
  expect(f.a.getState().resolution).toBe('');
  await f.q.ensureQueue();
  await f.q.openCase(caseId(4));
  expect(f.a.getState()).toMatchObject({
    resolution: 'The parking team confirmed the extension.',
    phase: 'review',
    candidate: null,
    closeConfirmation: false,
  });
});
it('rejects unbound, excessive and over-disclosing candidate envelopes', async () => {
  const f = await setup();
  const c = f.q.getState().capabilities!,
    reference = 'CASE-' + caseId(2).replaceAll('-', '');
  const base = {
    case_id: c.case_id,
    case_version: c.case_version,
    membership_id: MEMBER,
    membership_version: c.membership_version,
    items: [{ case_id: caseId(2), public_reference: reference }],
  };
  expect(decodeCandidates(base, c, reference)?.case_id).toBe(caseId(2));
  for (const bad of [
    { ...base, case_version: 99 },
    { ...base, items: [...base.items, ...base.items] },
    { ...base, items: [{ ...base.items[0], summary: 'private' }] },
    { ...base, items: [{ ...base.items[0], case_id: c.case_id }] },
  ])
    expect(() => decodeCandidates(bad, c, reference)).toThrow();
});
it('active conversation version is included without any accept/send request', async () => {
  const f = await setup();
  const cap = structuredClone(f.q.getState().capabilities!);
  cap.active_conversation = { id: caseId(60), version: 7, mode: 'human' };
  let body: Record<string, unknown> = {};
  f.server.custom = (path, init) => {
    if (path.includes('/capabilities')) return Promise.resolve(f.json(cap));
    if (init?.method === 'PATCH') body = JSON.parse(String(init.body));
    return null;
  };
  await f.q.openCase(caseId(4), true);
  f.a.requestClose();
  await f.a.confirmClose();
  expect(body.expected_conversation_version).toBe(7);
  expect(f.server.requests.some((r) => r.path.includes('/accept'))).toBe(false);
});
