import { afterEach, expect, it } from 'vitest';
import {
  canUseDraft,
  toForm,
  toPayload,
} from '../../src/features/cases/contracts';
import { submissionFixture, DRAFT } from './submission-fixture';
import { ID, error, payload } from './fixture';

const owned: ReturnType<typeof submissionFixture>[] = [];
afterEach(() => owned.splice(0).forEach((f) => f.dispose()));
const general = () => ({
  ...payload,
  category: 'information' as const,
  service_type: 'general' as const,
  booking_reference: null,
  transaction_reference: null,
  contact_email: null,
});
async function setup(kind: 'anonymous' | 'customer' = 'anonymous') {
  const f = submissionFixture();
  owned.push(f);
  f.server.kind = kind;
  f.server.principal = kind === 'anonymous' ? null : ID;
  f.previewServer.draft = {
    ...f.previewServer.draft,
    action_type: 'request_handoff',
    payload: general(),
  };
  await f.auth.start();
  await f.store.open(ID);
  f.store.input({
    body: 'Please help me find the public waiting area.',
    service: '',
  });
  return f;
}
it('anonymous general handoff prepares a preview only; explicit confirm alone submits its sealed version/hash', async () => {
  const f = await setup();
  await f.cases.prepareFromChat('information', '', 'request_handoff');
  expect(f.submits.commits).toBe(0);
  expect(await f.cases.create()).toBe(DRAFT);
  expect(f.previewServer.bodies[0]).toMatchObject({
    action_type: 'request_handoff',
    payload: {
      category: 'information',
      service_type: 'general',
      contact_email: null,
      booking_reference: null,
      transaction_reference: null,
      customer_report: 'Please help me find the public waiting area.',
    },
  });
  expect(f.submits.commits).toBe(0);
  await f.submission.open(DRAFT);
  await f.submission.confirm(f.intent());
  expect(f.submits.commits).toBe(1);
  expect(f.submits.requests[0]?.body).toEqual({
    expected_version: 1,
    payload_hash: 'a'.repeat(64),
  });
  expect(f.submits.requests[0]?.key).toBeTruthy();
  expect(
    f.fetcher.mock.calls.some(([path]) => String(path).includes('/agent-runs')),
  ).toBe(false);
});
it.each([
  ['category', 'booking_issue'],
  ['category', 'transaction_issue'],
  ['service_type', 'parking'],
  ['service_type', 'lounge'],
  ['booking_reference', 'BOOK-1'],
  ['transaction_reference', 'PAY-1'],
  ['contact_email', 'someone@example.com'],
] as const)(
  'anonymous confirmation rejects broadened %s=%s without POST',
  async (field, value) => {
    const f = await setup();
    Object.assign(f.previewServer.draft.payload, { [field]: value });
    await f.submission.open(DRAFT);
    await f.submission.confirm(f.intent());
    expect(f.submits.requests).toHaveLength(0);
  },
);
it('anonymous create_case and staff identity do not inherit the handoff exception', () => {
  expect(canUseDraft('anonymous', 'request_handoff', general())).toBe(true);
  expect(canUseDraft('anonymous', 'create_case', general())).toBe(false);
  expect(canUseDraft('staff', 'request_handoff', general())).toBe(false);
  expect(canUseDraft('anonymous', 'request_handoff', general(), ID)).toBe(
    false,
  );
});
it('email is optional only for handoff, while report-only create_case still requires it', () => {
  const form = toForm(general());
  expect(toPayload(form, 'request_handoff', true).contact_email).toBeNull();
  expect(toPayload(form, 'request_handoff', false).contact_email).toBeNull();
  expect(() => toPayload(form)).toThrow();
  expect(() =>
    toPayload(
      { ...form, booking_reference: 'BOOK-1' },
      'request_handoff',
      true,
    ),
  ).toThrow();
});
it('verified booking handoff preserves report references, with backend verified-contact check still authoritative', async () => {
  const f = await setup('customer');
  await f.cases.prepareFromChat('booking', '', 'request_handoff');
  f.cases.input({ ...f.cases.getState().form, booking_reference: 'BOOK-1' });
  await f.cases.create();
  expect(f.previewServer.bodies[0]).toMatchObject({
    action_type: 'request_handoff',
    payload: {
      category: 'booking_issue',
      booking_reference: 'BOOK-1',
      contact_email: null,
    },
  });
  await f.submission.open(DRAFT);
  f.submits.handler = (path) =>
    path.endsWith('/confirm')
      ? Promise.resolve(error(422, 'CASE_CONTACT_VERIFICATION_REQUIRED'))
      : null;
  await f.submission.confirm(f.intent());
  expect(f.submission.getState().phase).not.toBe('receipt');
});
it('repeated preparation reuses known preview and never switches a pending preview into report-only creation', async () => {
  const f = await setup();
  await f.cases.prepareFromChat('information', '', 'request_handoff');
  await f.cases.create();
  await f.cases.prepareFromChat('information', '', 'request_handoff');
  expect(await f.cases.create()).toBeNull();
  expect(f.previewServer.creates).toBe(1);
  expect(f.cases.getState().knownDraftId).toBe(DRAFT);
});
it('unknown draft creation does not retry or create another handoff', async () => {
  const f = await setup();
  await f.cases.prepareFromChat('information', '', 'request_handoff');
  f.previewServer.handler = (method) =>
    method === 'POST' ? Promise.reject(Error('lost response')) : null;
  await f.cases.create();
  expect(f.cases.getState().uncertain?.kind).toBe('create');
  const before = f.fetcher.mock.calls.length;
  await f.cases.create();
  expect(f.fetcher.mock.calls).toHaveLength(before);
});
it('session adoption cancels old preview and old confirmation intent cannot execute under the new owner', async () => {
  const f = await setup();
  await f.submission.open(DRAFT);
  const old = f.intent();
  f.server.kind = 'customer';
  f.server.principal = ID;
  f.server.proof = 'q'.repeat(43);
  f.previewServer.draft.state = 'cancelled';
  await f.auth.recheck();
  await f.submission.open(DRAFT);
  await f.submission.confirm(old);
  expect(f.submits.requests).toHaveLength(0);
  expect(f.cases.getState().draft?.state).toBe('cancelled');
});
it('anonymous editor preserves null contact and cannot expand references; verified edit receives a fresh server hash', async () => {
  const f = await setup();
  await f.cases.open(DRAFT);
  f.cases.beginEdit();
  f.cases.input({
    ...f.cases.getState().form,
    summary: 'Corrected public waiting question',
  });
  await f.cases.update();
  expect(f.previewServer.patches).toBe(1);
  expect(f.cases.getState().draft).toMatchObject({
    version: 2,
    payload_hash: 'b'.repeat(64),
    action_type: 'request_handoff',
    payload: { contact_email: null },
  });
});
it('lost confirm response uses authorized action status instead of automatically resubmitting', async () => {
  const f = await setup();
  await f.submission.open(DRAFT);
  f.submits.loseResponse = true;
  await f.submission.confirm(f.intent());
  expect(f.submits.commits).toBe(1);
  await f.submission.recover();
  expect(f.submission.getState().phase).toBe('receipt');
  expect(f.submits.requests).toHaveLength(1);
});
