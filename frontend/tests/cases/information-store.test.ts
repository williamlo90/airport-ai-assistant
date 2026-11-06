import { afterEach, expect, it } from 'vitest';
import { fixture, json, error, ID, OTHER } from '../chat/fixture';
import { CASE } from './detail-fixture';
import { CustomerInformationStore } from '../../src/features/cases/information-store';
import { informationMessage } from '../../src/features/cases/customer-contracts';
const owned: (() => void)[] = [];
afterEach(() => owned.splice(0).forEach((off) => off()));
async function setup() {
  const f = fixture();
  f.server.kind = 'customer';
  f.server.principal = OTHER;
  await f.auth.start();
  const information = new CustomerInformationStore(f.auth),
    off = information.connect();
  owned.push(() => {
    off();
    f.dispose();
  });
  information.open(CASE, 1, false);
  const requests: {
    path: string;
    method: string;
    body: Record<string, unknown>;
    key: string | null;
  }[] = [];
  const server = {
    lost: false,
    saved: false,
    unavailable: false,
    receiptMismatch: false,
  };
  f.server.override = async (path, init) => {
    if (path === '/api/v1/me')
      return json({
        session_kind: f.server.kind,
        principal_id: f.server.principal,
      });
    const method = init?.method ?? 'GET',
      body = JSON.parse(String(init?.body ?? '{}'));
    requests.push({
      path,
      method,
      body,
      key: new Headers(init?.headers).get('Idempotency-Key'),
    });
    if (server.unavailable) return error(503, 'SOURCE_UNAVAILABLE');
    if (path.endsWith('/information-previews'))
      return json({
        ...body,
        case_id: CASE,
        preview_id: ID,
        expires_at: Math.floor(Date.now() / 1000) + 900,
        confirmation_token: 'private-review-token',
      });
    if (path.endsWith('/information')) {
      server.saved = true;
      if (server.lost) throw Error('response lost');
    }
    if (!server.saved) return error(404, 'RESOURCE_NOT_FOUND');
    return json(
      {
        state: 'saved',
        case_id: CASE,
        preview_id: server.receiptMismatch ? OTHER : ID,
        message_id: OTHER,
        sequence: 7,
        case_version: 2,
      },
      method === 'POST' ? 201 : 200,
    );
  };
  return { ...f, information, requests, infoServer: server };
}
it('requires exact review and explicit confirmation, then sends only token/confirmation with one stable operation key', async () => {
  const f = await setup();
  f.information.edit('Original information.');
  await f.information.review('en');
  expect(f.requests[0]?.body).toEqual({
    expected_version: 1,
    message: 'Original information.',
    language: 'en',
  });
  await f.information.confirm(false);
  expect(f.requests).toHaveLength(1);
  await f.information.confirm(true);
  await f.information.confirm(true);
  expect(f.requests).toHaveLength(2);
  expect(f.requests[1]).toMatchObject({
    method: 'POST',
    body: { confirmation_token: 'private-review-token', confirmed: true },
  });
  expect(f.requests[1]?.key).toMatch(/^[a-f0-9-]{36}$/);
  expect(f.information.getState()).toMatchObject({
    phase: 'saved',
    message: '',
    preview: null,
    receipt: { sequence: 7, case_version: 2 },
  });
});
it('lost confirmation keeps only IDs and recovers by GET without reposting, even after case closure', async () => {
  const f = await setup();
  f.information.edit('Evidence for the same issue.');
  await f.information.review('en');
  f.infoServer.lost = true;
  await f.information.confirm(true);
  expect(f.information.getState()).toMatchObject({
    phase: 'unknown',
    message: '',
    preview: null,
  });
  f.information.discard();
  f.information.edit('Do not duplicate');
  await f.information.review('en');
  expect(f.requests).toHaveLength(2);
  f.information.open(CASE, 4, true);
  await f.information.recover();
  expect(f.requests.at(-1)?.path).toBe(
    `/api/v1/cases/${CASE}/information-receipts/${f.requests[1]?.key}`,
  );
  expect(f.requests.at(-1)?.method).toBe('GET');
  expect(f.information.getState().phase).toBe('saved');
});
it('a missing or mismatched receipt stays uncertain and cannot permit another submission', async () => {
  const f = await setup();
  f.information.edit('Original');
  await f.information.review('en');
  f.infoServer.lost = true;
  await f.information.confirm(true);
  f.infoServer.saved = false;
  await f.information.recover();
  expect(f.information.getState().phase).toBe('unknown');
  f.infoServer.saved = true;
  f.infoServer.receiptMismatch = true;
  await f.information.recover();
  expect(f.information.getState().phase).toBe('unknown');
  expect(
    f.requests.filter((r) => r.path.endsWith('/information')),
  ).toHaveLength(1);
});
it('purges a denied review and rejects altered server review text before confirmation', async () => {
  const f = await setup();
  f.information.edit('Original');
  f.server.override = async () =>
    json({
      case_id: CASE,
      preview_id: ID,
      message: 'Altered',
      language: 'en',
      expected_version: 1,
      expires_at: Math.floor(Date.now() / 1000) + 900,
      confirmation_token: 'secret',
    });
  await f.information.review('en');
  expect(f.information.getState().preview).toBeNull();
  f.server.override = async () => error(404, 'RESOURCE_NOT_FOUND');
  await f.information.review('en');
  expect(f.information.getState()).toMatchObject({
    phase: 'idle',
    message: '',
    preview: null,
  });
});
it('source failure purges review/body while retaining an uncertain confirmation ID for a fresh GET', async () => {
  const f = await setup();
  f.information.edit('Private original');
  await f.information.review('en');
  f.infoServer.unavailable = true;
  await f.information.confirm(true);
  expect(f.information.getState()).toMatchObject({
    phase: 'unknown',
    message: '',
    preview: null,
    receipt: null,
  });
  f.infoServer.unavailable = false;
  f.infoServer.saved = true;
  await f.information.recover();
  expect(f.information.getState().phase).toBe('saved');
});
it('case version changes invalidate review, and a closed case cannot submit additional text', async () => {
  const f = await setup();
  f.information.edit('Original');
  await f.information.review('en');
  f.information.open(CASE, 2, false);
  await f.information.confirm(true);
  expect(f.requests).toHaveLength(1);
  expect(f.information.getState().preview).toBeNull();
  f.information.open(CASE, 3, true);
  f.information.edit('Closed');
  await f.information.review('en');
  expect(f.requests).toHaveLength(1);
});
it('auth reset rejects a late preview and clears all private state', async () => {
  const f = await setup();
  let finish!: (response: Response) => void;
  f.server.override = () =>
    new Promise<Response>((resolve) => {
      finish = resolve;
    });
  f.information.edit('Private original');
  const pending = f.information.review('en');
  f.server.proof = 'q'.repeat(43);
  expect(() => f.auth.capture()).toThrow();
  finish(
    json({
      case_id: CASE,
      preview_id: ID,
      message: 'Private original',
      language: 'en',
      expected_version: 1,
      expires_at: Math.floor(Date.now() / 1000) + 900,
      confirmation_token: 'secret',
    }),
  );
  await pending;
  expect(f.information.getState()).toMatchObject({
    phase: 'idle',
    message: '',
    preview: null,
  });
});
it('same-session recheck purges review but preserves ID-only uncertain recovery, and changed principals cannot recover it', async () => {
  const f = await setup();
  f.information.edit('Original');
  await f.information.review('en');
  await f.auth.recheck();
  expect(f.information.getState()).toMatchObject({
    phase: 'idle',
    preview: null,
    message: '',
  });
  f.information.edit('Original');
  await f.information.review('en');
  f.infoServer.lost = true;
  await f.information.confirm(true);
  await f.auth.recheck();
  await f.information.recover();
  expect(f.information.getState().phase).toBe('saved');
  expect(
    f.requests.filter((r) => r.path.endsWith('/information')),
  ).toHaveLength(1);
  f.information.edit('Next');
  await f.information.review('en');
  await f.information.confirm(true);
  f.server.principal = ID;
  await f.auth.recheck();
  const before = f.requests.length;
  await f.information.recover();
  expect(f.requests).toHaveLength(before);
  expect(f.information.getState()).toMatchObject({
    phase: 'idle',
    preview: null,
    message: '',
  });
});
it('uses Unicode character limits and rejects blank/NUL/lone surrogate messages', () => {
  expect(informationMessage.safeParse('🙂'.repeat(4000)).success).toBe(true);
  for (const value of ['', '  ', '\0', '\uD800', 'x'.repeat(4001)])
    expect(informationMessage.safeParse(value).success).toBe(false);
});
it('proof rotation immediately before a command is handled without a rejected UI promise or POST', async () => {
  const f = await setup();
  f.information.edit('Original');
  f.server.proof = 'q'.repeat(43);
  await expect(f.information.review('en')).resolves.toBeUndefined();
  expect(f.requests).toHaveLength(0);
  expect(f.information.getState().preview).toBeNull();
});
