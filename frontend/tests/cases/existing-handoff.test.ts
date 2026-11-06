import { afterEach, expect, it } from 'vitest';
import { caseFixture, DRAFT, NEXT, ID, json, error } from './fixture';
import { closedCase, CASE, NEXT_CASE } from './detail-fixture';
import type { CaseDetail } from '../../src/features/cases/detail-contracts';
import {
  localizedTarget,
  readHandoffCase,
  shouldBlockDirtyNavigation,
} from '../../src/app/routing/policy';
const owned: (() => void)[] = [];
afterEach(() => owned.splice(0).forEach((fn) => fn()));
async function setup() {
  const f = caseFixture();
  owned.push(f.dispose);
  f.server.kind = 'customer';
  f.server.principal = ID;
  await f.auth.start();
  await f.store.open(ID);
  f.store.input({
    body: 'Please help me with the same parking case.',
    service: 'parking',
  });
  const linked: CaseDetail = {
    ...closedCase(),
    status: 'in_progress',
    close_reason: null,
    resolution: null,
    closed_at: null,
  };
  const base = f.fetcher.getMockImplementation()!;
  f.fetcher.mockImplementation(async (input, init) =>
    String(input) === '/api/v1/cases/' + CASE
      ? json(linked)
      : base(input, init),
  );
  f.previewServer.draft.action_type = 'request_handoff';
  f.previewServer.draft.case_id = CASE;
  return { ...f, linked };
}
it('loads the owned open case and binds the reviewed draft to it without changing its contact or confirming', async () => {
  const f = await setup();
  await f.cases.prepareFromChat(
    'contact',
    'different@example.com',
    'request_handoff',
    CASE,
  );
  expect(f.cases.getState()).toMatchObject({
    phase: 'intake',
    handoffCaseId: CASE,
    form: { contact_email: '' },
  });
  f.cases.input({
    ...f.cases.getState().form,
    contact_email: 'attempted-change@example.com',
  });
  expect(await f.cases.create()).toBe(DRAFT);
  expect(f.previewServer.bodies[0]).toMatchObject({
    case_id: CASE,
    action_type: 'request_handoff',
    payload: { contact_email: null },
  });
  expect(
    f.fetcher.mock.calls.some(([path]) => String(path).includes('/confirm')),
  ).toBe(false);
});
it('a closed or inaccessible case cannot produce a preview or a replacement case', async () => {
  const f = await setup();
  f.linked.status = 'closed';
  f.linked.close_reason = 'resolved';
  f.linked.resolution = 'Recorded synthetic result.';
  f.linked.closed_at = '2026-11-04T08:00:00Z';
  await f.cases.prepareFromChat('contact', '', 'request_handoff', CASE);
  expect(f.cases.getState().phase).toBe('unavailable');
  expect(await f.cases.create()).toBeNull();
  expect(f.previewServer.creates).toBe(0);
  f.fetcher.mockImplementation(async () => error(404, 'RESOURCE_NOT_FOUND'));
  await f.cases.prepareFromChat('contact', '', 'request_handoff', NEXT_CASE);
  expect(f.previewServer.creates).toBe(0);
});
it('a server preview for another case is not accepted as a successful linked request', async () => {
  const f = await setup();
  await f.cases.prepareFromChat('contact', '', 'request_handoff', CASE);
  f.previewServer.draft.case_id = NEXT_CASE;
  expect(await f.cases.create()).toBeNull();
  expect(f.cases.getState().uncertain).not.toBeNull();
  expect(f.cases.getState().draft).toBeNull();
});
it('case selection is a bounded route hint and changing it requires a dirty-navigation decision', () => {
  expect(readHandoffCase('?case=' + CASE)).toBe(CASE);
  expect(readHandoffCase('?case=' + CASE.toUpperCase())).toBe(CASE);
  expect(localizedTarget('/help/verify?case=' + CASE, 'en')).toContain(
    'case=' + CASE,
  );
  expect(
    readHandoffCase('?case=' + CASE + '&case=' + NEXT_CASE),
  ).toBeUndefined();
  expect(readHandoffCase('?case=../other')).toBeUndefined();
  expect(
    localizedTarget('/help/new?topic=contact&case=' + CASE, 'en'),
  ).toContain('case=' + CASE);
  expect(localizedTarget('/staff/queue?case=' + CASE, 'en')).not.toContain(
    'case=',
  );
  expect(
    shouldBlockDirtyNavigation(
      { pathname: '/help/new', search: '?topic=contact&case=' + CASE },
      { pathname: '/help/new', search: '?topic=contact&case=' + NEXT_CASE },
    ),
  ).toBe(true);
});
it('unknown create preserves the selected case on intake re-entry and cannot be redirected to another case', async () => {
  const f = await setup();
  await f.cases.prepareFromChat('contact', '', 'request_handoff', CASE);
  const original = f.fetcher.getMockImplementation()!;
  f.fetcher.mockImplementation(async (input, init) => {
    const result = await original(input, init);
    if (String(input) === '/api/v1/action-drafts' && init?.method === 'POST')
      throw Error('Synthetic lost response');
    return result;
  });
  expect(await f.cases.create()).toBeNull();
  expect(f.previewServer.creates).toBe(1);
  f.fetcher.mockImplementation(async (input, init) =>
    String(input) === '/api/v1/cases/' + NEXT_CASE
      ? json({
          ...f.linked,
          id: NEXT_CASE,
          public_reference: 'CASE-' + NEXT_CASE.replaceAll('-', ''),
        })
      : original(input, init),
  );
  await f.cases.enterIntake();
  expect(f.cases.getState()).toMatchObject({
    handoffCaseId: CASE,
    uncertain: { kind: 'create' },
  });
  await f.cases.prepareFromChat('contact', '', 'request_handoff', NEXT_CASE);
  expect(f.cases.getState().phase).toBe('unavailable');
  expect(await f.cases.create()).toBeNull();
  expect(f.previewServer.creates).toBe(1);
});
it('renewing an expired linked preview keeps the same case and requires a new explicit create', async () => {
  const f = await setup();
  await f.cases.prepareFromChat('contact', '', 'request_handoff', CASE);
  await f.cases.create();
  f.previewServer.draft.state = 'expired';
  f.previewServer.draft.expires_at = '2026-01-01T00:00:00Z';
  expect(await f.cases.renew()).toBe(true);
  expect(f.cases.getState()).toMatchObject({
    phase: 'intake',
    handoffCaseId: CASE,
  });
  expect(f.previewServer.creates).toBe(1);
  f.previewServer.draft = {
    ...f.previewServer.draft,
    draft_id: NEXT,
    state: 'pending',
    expires_at: new Date(Date.now() + 600000).toISOString(),
  };
  expect(await f.cases.create()).toBe(NEXT);
  expect(f.previewServer.bodies.at(-1)).toMatchObject({
    case_id: CASE,
    payload: { contact_email: null },
  });
});
it('source outage before owned-case lookup finishes prevents a late response from restoring the selection', async () => {
  const f = await setup();
  const original = f.fetcher.getMockImplementation()!;
  let resolve!: (value: Response) => void;
  f.fetcher.mockImplementation((input, init) =>
    String(input) === '/api/v1/cases/' + CASE
      ? new Promise<Response>((r) => {
          resolve = r;
        })
      : original(input, init),
  );
  const preparing = f.cases.prepareFromChat(
    'contact',
    '',
    'request_handoff',
    CASE,
  );
  for (let index = 0; index < 20 && !resolve; index++) await Promise.resolve();
  f.server.override = async () => error(503, 'SOURCE_UNAVAILABLE');
  await f.auth
    .request({
      method: 'GET',
      path: '/source/check',
      boundary: f.auth.capture(),
      isRelevant: () => true,
      successStatus: 200,
      decode: (v) => v,
    })
    .catch(() => {});
  resolve(json(f.linked));
  await preparing;
  expect(f.cases.getState()).toMatchObject({
    phase: 'unavailable',
    handoffCaseId: null,
    draft: null,
  });
  expect(f.previewServer.creates).toBe(0);
});
