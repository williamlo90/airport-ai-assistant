import { afterEach, expect, it, vi } from 'vitest';
import { verificationFixture, ID, OTHER, json } from './fixture';
const owned: ReturnType<typeof verificationFixture>[] = [];
const setup = (now?: () => number) => {
  const f = verificationFixture(now);
  owned.push(f);
  return f;
};
afterEach(() => {
  owned.splice(0).forEach((f) => f.dispose());
  vi.useRealTimers();
});
async function report(f: ReturnType<typeof setup>) {
  await f.auth.start();
  await f.store.create('en');
  f.store.input({ body: 'My original report', service: 'parking' });
}
it('normal503 does not claim requested code or delivery; keeps legitimate report for retry', async () => {
  const f = setup();
  await report(f);
  f.scenario.challengeStatus = 503;
  await f.verify.request('owner@example.com');
  expect(f.verify.getState()).toMatchObject({
    phase: 'email',
    challengeId: null,
    contactVerified: false,
    error: { code: 'VERIFICATION_UNAVAILABLE' },
    report: { body: 'My original report', service: 'parking' },
  });
  expect(f.scenario.requests).toBe(1);
});
it('success carries only plaintext into fresh explicit review, no old commands or automatic create/send', async () => {
  const f = setup();
  await report(f);
  await f.store.send();
  f.store.input({ body: 'Extra original draft', service: 'lounge' });
  await f.verify.request('owner@example.com');
  const old = f.auth.capture();
  await f.verify.verify('12345678');
  const state = f.verify.getState();
  expect(state).toMatchObject({
    phase: 'review',
    contact: 'owner@example.com',
    contactVerified: true,
    report: {
      body: 'parking\n\nMy original report\n\nExtra original draft',
      service: 'lounge',
    },
  });
  expect(Object.keys(state.report!).sort()).toEqual(['body', 'service']);
  expect(f.auth.isCurrent(old)).toBe(false);
  expect(f.store.getState().messages).toHaveLength(0);
  expect(f.server.creates).toBe(1);
  expect(f.server.posts).toBe(1);
  await f.verify.continue('en');
  expect(f.server.creates).toBe(2);
  expect(f.server.posts).toBe(1);
  expect(f.store.getState().input.body).toBe(state.report!.body);
  await f.verify.continue('en');
  expect(f.server.creates).toBe(2);
});
it('generic wrong/expired422 keeps same-origin plaintext/challenge for explicit retry, not old commands', async () => {
  const f = setup();
  await report(f);
  await f.verify.request('owner@example.com');
  f.scenario.verifyStatus = 422;
  await f.verify.verify('00000000');
  expect(f.verify.getState()).toMatchObject({
    phase: 'code',
    challengeId: ID,
    contactVerified: false,
    error: { code: 'VERIFICATION_FAILED' },
    report: { body: 'My original report' },
  });
  f.scenario.verifyStatus = 200;
  await f.verify.verify('12345678');
  expect(f.verify.getState().contactVerified).toBe(true);
  expect(f.scenario.verifies).toBe(2);
});
it('429 RetryAfter blocks attempts without inventing OTP expiry and survives edit/recheck', async () => {
  vi.useFakeTimers();
  let now = 1000;
  const f = setup(() => now);
  await report(f);
  f.scenario.challengeStatus = 429;
  await f.verify.request('owner@example.com');
  expect(f.verify.getState().blockedUntil).toBe(4000);
  await f.verify.request('owner@example.com');
  expect(f.scenario.requests).toBe(1);
  f.verify.editContact();
  await f.auth.recheck();
  expect(f.verify.blocked()).toBe(true);
  now = 4000;
  await vi.advanceTimersByTimeAsync(3000);
  f.scenario.challengeStatus = 202;
  await f.verify.request('owner@example.com');
  expect(f.scenario.requests).toBe(2);
  expect(f.verify.getState()).toMatchObject({
    phase: 'code',
    blockedUntil: null,
  });
});
it('resend/change replace challenge explicitly and customer never uses anonymous OTP', async () => {
  const f = setup();
  await report(f);
  await f.verify.request('owner@example.com');
  await f.verify.request('owner@example.com');
  expect(f.verify.getState().challengeId).toBe(OTHER);
  f.verify.editContact();
  expect(f.verify.getState().challengeId).toBeNull();
  f.server.kind = 'customer';
  f.server.principal = OTHER;
  f.server.proof = 'q'.repeat(43);
  await f.auth.recheck();
  const before = f.scenario.requests;
  await expect(f.verify.request('another@example.com')).rejects.toBeDefined();
  f.verify.useExisting('existing@example.com');
  expect(f.verify.getState()).toMatchObject({
    phase: 'review',
    contactVerified: false,
    contact: 'existing@example.com',
  });
  expect(f.scenario.requests).toBe(before);
});
it('unknown verify result plus customer inspection does not prove contact or grant report resume', async () => {
  const f = setup();
  await report(f);
  await f.verify.request('owner@example.com');
  f.scenario.unknownVerify = true;
  await f.verify.verify('12345678');
  expect(f.auth.getState()).toMatchObject({ phase: 'ready', kind: 'customer' });
  expect(f.verify.getState()).toMatchObject({
    phase: 'unknown',
    contact: '',
    report: null,
    contactVerified: false,
  });
  expect(await f.verify.continue('en')).toBeNull();
});
it('soft focus recheck keeps same-owner challenge and later verified review grant', async () => {
  const f = setup();
  await report(f);
  await f.verify.request('owner@example.com');
  await f.auth.recheck();
  expect(f.verify.getState()).toMatchObject({
    phase: 'code',
    challengeId: ID,
    contact: 'owner@example.com',
  });
  await f.verify.verify('12345678');
  await f.auth.recheck();
  expect(f.verify.getState()).toMatchObject({
    phase: 'review',
    contactVerified: true,
  });
  expect(await f.verify.continue('en')).toBe(ID);
});
it('account replacement clears email/challenge/report instead of restoring previous actor input', async () => {
  const f = setup();
  await report(f);
  await f.verify.request('owner@example.com');
  f.server.proof = 'q'.repeat(43);
  f.server.kind = 'customer';
  f.server.principal = OTHER;
  await f.auth.recheck();
  expect(f.verify.getState()).toMatchObject({
    phase: 'email',
    contact: '',
    challengeId: null,
    report: null,
    contactVerified: false,
  });
});
it('explicit discard during verify prevents late success from resurrecting contact/report', async () => {
  const f = setup();
  await report(f);
  await f.verify.request('owner@example.com');
  let resolve!: (v: Response) => void;
  f.scenario.delay = () =>
    new Promise((r) => {
      resolve = r;
    });
  const pending = f.verify.verify('12345678');
  await Promise.resolve();
  f.verify.discard();
  f.server.proof = 'r'.repeat(43);
  f.server.kind = 'customer';
  f.server.principal = OTHER;
  resolve(json({ session_kind: 'customer', principal_id: OTHER }));
  await pending;
  expect(f.verify.getState().contact).toBe('');
  expect(f.verify.getState().report).toBeNull();
  expect(f.verify.getState().contactVerified).toBe(false);
});
it('oversized retained story is kept for explicit editing and not truncated or submitted', async () => {
  const f = setup();
  await report(f);
  await f.verify.request('owner@example.com');
  await f.verify.verify('12345678');
  f.verify.review('x'.repeat(5000));
  expect(await f.verify.continue('en')).toBeNull();
  expect(f.verify.getState().report?.body).toHaveLength(5000);
  expect(f.server.creates).toBe(1);
});

it('focus check during explicit continuation shows creation unknown instead of an endless spinner or replacement', async () => {
  const f = setup();
  await report(f);
  await f.verify.request('owner@example.com');
  await f.verify.verify('12345678');
  let finish!: (value: Response) => void;
  let posts = 0;
  f.server.override = async (path, init) => {
    if (path === '/api/v1/conversations' && init?.method === 'POST') {
      posts++;
      return new Promise((r) => {
        finish = r;
      });
    }
    return json({ session_kind: 'customer', principal_id: OTHER });
  };
  const pending = f.verify.continue('en');
  await Promise.resolve();
  await f.auth.recheck();
  expect(f.verify.getState()).toMatchObject({
    phase: 'continue-unknown',
    contactVerified: true,
  });
  finish(json(f.server.meta, 201));
  await pending;
  expect(await f.verify.continue('en')).toBeNull();
  expect(posts).toBe(1);
});
