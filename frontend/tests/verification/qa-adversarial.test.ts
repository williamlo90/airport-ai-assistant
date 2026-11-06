import { expect, it, vi } from 'vitest';
import { AuthCoordinator } from '../../src/app/auth/coordinator';
import { ChatStore } from '../../src/features/chat/store';
import { VerificationStore } from '../../src/features/verification/store';

const id = '11111111-1111-4111-8111-111111111111';
const principal = '22222222-2222-4222-8222-222222222222';
const json = (v: unknown, status = 200) =>
  new Response(JSON.stringify(v), {
    status,
    headers: {
      'Content-Type': 'application/json',
      ...(status === 429 ? { 'Retry-After': '60' } : {}),
    },
  });
function fixture(customer = false) {
  const server = {
    kind: customer ? 'customer' : 'anonymous',
    proof: 'p'.repeat(43),
    creates: 0,
    defer: false,
    challengeStatus: 202,
    verifyStatus: 200,
    loseVerify: false,
    challengePosts: 0,
    resourceId: id,
    finish: null as null | (() => void),
  };
  const meta = {
    conversation_id: id,
    language: 'en',
    version: 1,
    generation_epoch: 0,
    last_sequence: 0,
  };
  const auth = new AuthCoordinator({
    readProof: () => server.proof,
    fetch: async (url, init) => {
      const path = String(url);
      const session = () => ({
        session_kind: server.kind,
        principal_id: server.kind === 'customer' ? principal : null,
      });
      if (path === '/api/v1/me') return json(session());
      if (path === '/api/v1/auth/challenges') {
        server.challengePosts++;
        return server.challengeStatus === 202
          ? json({ challenge_id: id, status: 'verification_requested' }, 202)
          : json({}, server.challengeStatus);
      }
      if (path.endsWith('/verify')) {
        if (server.verifyStatus !== 200) return json({}, server.verifyStatus);
        const upgrade = () => {
          server.kind = 'customer';
          server.proof = 'q'.repeat(43);
          if (server.loseVerify) throw new Error('synthetic response lost');
          return json(session());
        };
        if (server.defer)
          return new Promise<Response>((resolve) => {
            server.finish = () => resolve(upgrade());
          });
        return upgrade();
      }
      if (path === '/api/v1/conversations' && init?.method === 'POST') {
        server.creates++;
        return json({ ...meta, conversation_id: server.resourceId }, 201);
      }
      return json({ ...meta, conversation_id: server.resourceId });
    },
  });
  const chat = new ChatStore(auth, 'qa');
  const offChat = chat.connect();
  const verification = new VerificationStore(auth, chat);
  const offVerify = verification.connect();
  return {
    server,
    auth,
    chat,
    verification,
    dispose() {
      offVerify();
      offChat();
      chat.dispose();
      auth.dispose();
    },
  };
}

it('an unknown verification response cannot badge a contact or promote text based only on customer me', async () => {
  const f = fixture();
  try {
    await f.auth.start();
    await f.chat.open(id);
    f.chat.input({ body: 'unconfirmed text', service: '' });
    await f.verification.request('synthetic@example.com');
    f.server.loseVerify = true;
    await f.verification.verify('12345678');
    expect(f.auth.getState()).toMatchObject({
      phase: 'ready',
      kind: 'customer',
    });
    expect(f.verification.getState()).toMatchObject({
      phase: 'unknown',
      contactVerified: false,
      contact: '',
      report: null,
    });
    expect(await f.verification.continue('en')).toBeNull();
    expect(f.server.creates).toBe(0);
  } finally {
    f.dispose();
  }
});

it('a generic422 preserves a same-origin challenge for explicit retry without replaying a report', async () => {
  const f = fixture();
  try {
    await f.auth.start();
    await f.chat.open(id);
    f.chat.input({ body: 'exact user report', service: 'parking' });
    await f.verification.request('synthetic@example.com');
    f.server.verifyStatus = 422;
    await f.verification.verify('12345678');
    expect(f.verification.getState()).toMatchObject({
      phase: 'code',
      challengeId: id,
      contact: 'synthetic@example.com',
      contactVerified: false,
      report: { body: 'exact user report', service: 'parking' },
    });
    f.server.verifyStatus = 200;
    await f.verification.verify('12345678');
    expect(f.verification.getState()).toMatchObject({
      phase: 'review',
      contactVerified: true,
    });
    expect(f.server.creates).toBe(0);
  } finally {
    f.dispose();
  }
});

it('Retry-After survives contact editing and a same-identity recheck, with no automatic resend', async () => {
  vi.useFakeTimers();
  const f = fixture();
  try {
    await f.auth.start();
    f.server.challengeStatus = 429;
    await f.verification.request('synthetic@example.com');
    expect(f.verification.blocked()).toBe(true);
    f.verification.editContact();
    await f.auth.recheck();
    f.verification.discard();
    expect(f.verification.blocked()).toBe(true);
    await f.verification.request('another@example.com');
    expect(f.server.challengePosts).toBe(1);
    await vi.advanceTimersByTimeAsync(60000);
    expect(f.verification.blocked()).toBe(false);
    expect(f.server.challengePosts).toBe(1);
    f.server.challengeStatus = 202;
    await f.verification.request('another@example.com');
    expect(f.server.challengePosts).toBe(2);
  } finally {
    f.dispose();
    vi.useRealTimers();
  }
});

it('anonymous bootstrap429 is surfaced and blocks another start until Retry-After', async () => {
  let posts = 0;
  const auth = new AuthCoordinator({
    readProof: () => null,
    fetch: async (url) => {
      if (String(url) === '/api/v1/me') return json({}, 401);
      posts++;
      return json({}, 429);
    },
  });
  const chat = new ChatStore(auth, 'qa');
  const offChat = chat.connect();
  const verification = new VerificationStore(auth, chat);
  const off = verification.connect();
  try {
    await auth.start();
    await verification.start();
    expect(verification.getState().error?.status).toBe(429);
    expect(verification.blocked()).toBe(true);
    await auth.recheck();
    expect(verification.blocked()).toBe(true);
    await verification.start();
    expect(posts).toBe(1);
  } finally {
    off();
    offChat();
    chat.dispose();
    auth.dispose();
  }
});

it('discard during verify cannot resurrect the captured private contact or report', async () => {
  const f = fixture();
  try {
    await f.auth.start();
    await f.chat.open(id);
    f.chat.input({ body: 'discard this private report', service: '' });
    await f.verification.request('synthetic@example.com');
    f.server.defer = true;
    const pending = f.verification.verify('12345678');
    f.verification.discard();
    f.server.finish!();
    await pending;
    expect(f.verification.getState().contact).toBe('');
    expect(f.verification.getState().report).toBeNull();
    expect(f.server.creates).toBe(0);
  } finally {
    f.dispose();
  }
});

it('a session recheck before requesting the first code must not lose the source story', async () => {
  const f = fixture();
  try {
    await f.auth.start();
    await f.chat.open(id);
    f.chat.input({
      body: 'keep my source story before requesting a code',
      service: 'parking',
    });
    // The chat link prepares its owned plaintext before navigating, without a request.
    f.verification.prepareFromChat();
    await f.auth.recheck();
    await f.verification.request('synthetic@example.com');
    expect(f.verification.getState().report?.body).toBe(
      'keep my source story before requesting a code',
    );
  } finally {
    f.dispose();
  }
});

it('fresh same-identity recheck does not leave a restored review with an unusable Continue action', async () => {
  const f = fixture(true);
  try {
    await f.auth.start();
    await f.chat.open(id);
    f.chat.input({ body: 'authorized report for review', service: 'parking' });
    f.verification.useExisting('synthetic@example.com');
    await f.auth.recheck();
    expect(f.verification.getState().phase).toBe('review');
    const result = await f.verification.continue('en');
    expect(result).toBe(id);
    expect(f.server.creates).toBe(1);
    expect(f.chat.getState().input.body).toBe('authorized report for review');
  } finally {
    f.dispose();
  }
});

it('a confirmed continuation is consumed instead of creating another conversation on repeated Continue', async () => {
  const f = fixture(true);
  try {
    await f.auth.start();
    await f.chat.open(id);
    f.chat.input({ body: 'review once', service: '' });
    f.verification.useExisting('synthetic@example.com');
    await f.verification.continue('en');
    await f.verification.continue('en');
    expect(f.server.creates).toBe(1);
  } finally {
    f.dispose();
  }
});

it('entry from a different conversation replaces the prior report and consumed continuation', async () => {
  const f = fixture(true);
  try {
    await f.auth.start();
    await f.chat.open(id);
    f.chat.input({ body: 'old flow report', service: '' });
    f.verification.prepareFromChat();
    f.verification.useExisting('synthetic@example.com');
    await f.verification.continue('en');
    expect(f.verification.getState().continuedId).toBe(id);
    f.chat.discard();
    f.server.resourceId = '33333333-3333-4333-8333-333333333333';
    await f.chat.open(f.server.resourceId);
    f.chat.input({ body: 'new flow report', service: 'lounge' });
    f.verification.prepareFromChat();
    expect(f.verification.getState()).toMatchObject({
      phase: 'email',
      contact: '',
      continuedId: null,
      challengeId: null,
      contactVerified: false,
      report: { body: 'new flow report', service: 'lounge' },
    });
    expect(await f.verification.continue('en')).toBeNull();
    expect(f.server.creates).toBe(1);
  } finally {
    f.dispose();
  }
});
