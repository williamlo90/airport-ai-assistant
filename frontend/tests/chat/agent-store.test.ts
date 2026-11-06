import { afterEach, expect, it, vi } from 'vitest';
import {
  AgentStore,
  type AgentBasis,
} from '../../src/features/chat/agent-store';
import { fixture, ID, OTHER, json, error } from './fixture';

const RUN = '33333333-3333-4333-8333-333333333333';
const owned: (() => void)[] = [];
afterEach(() => {
  owned.splice(0).forEach((dispose) => dispose());
  vi.useRealTimers();
});
async function setup(customer = false) {
  const f = fixture();
  if (customer) {
    f.server.kind = 'customer';
    f.server.principal = OTHER;
  }
  await f.auth.start();
  const state = {
    held: false,
    compatible: true,
    available: true,
    basis: {
      conversationId: ID,
      messageId: OTHER,
      version: 1,
      epoch: 0,
      language: 'en',
    } as AgentBasis,
    failStart: false,
    failAnswer: false,
    outcome: {
      run_id: RUN,
      state: 'published',
      message_id: ID,
      sequence: 2,
    } as unknown,
  };
  const refresh = vi.fn();
  const agent = new AgentStore(f.auth, {
    basis: () => (!state.held && state.available ? state.basis : null),
    compatible: () => state.compatible,
    hold: () => {
      state.held = true;
      return () => {
        state.held = false;
      };
    },
    refresh,
  });
  const off = agent.connect();
  f.server.override = async (path) => {
    if (path.endsWith('/agent-capabilities'))
      return json({ private_assistance: true });
    if (path.endsWith('/me'))
      return json({
        session_kind: f.server.kind,
        principal_id: f.server.principal,
      });
    if (path === '/api/v1/conversations/' + ID)
      return json({
        conversation_id: ID,
        version: state.basis.version,
        generation_epoch: state.basis.epoch,
        language: state.basis.language,
        last_sequence: 1,
      });
    if (path.endsWith('/agent-runs')) {
      if (state.failStart) throw Error('response lost');
      return json({ run_id: RUN, state: 'prepared' }, 201);
    }
    if (path.endsWith('/resume')) {
      if (state.failAnswer) throw Error('response lost');
      return json(state.outcome);
    }
    return error(404, 'RESOURCE_NOT_FOUND');
  };
  agent.attach(ID, 'route-a');
  owned.push(() => {
    off();
    agent.reset();
    f.dispose();
  });
  return { ...f, state, agent, refresh };
}

it('private references require a customer session and remain explicit bounded scope hints', async () => {
  const f = await setup();
  const refs = {
    booking_references: ['BOOK-1'],
    transaction_references: ['PAY-1'],
  };
  await f.agent.ask('parking', refs);
  expect(posts(f)).toHaveLength(0);
  f.server.override = null;
  f.server.kind = 'customer';
  f.server.principal = OTHER;
  await f.auth.recheck();
  f.server.override = async (path) => {
    if (path.endsWith('/agent-capabilities'))
      return json({ private_assistance: true });
    if (path.endsWith('/me'))
      return json({ session_kind: 'customer', principal_id: OTHER });
    if (path.endsWith('/agent-runs'))
      return json({ run_id: RUN, state: 'prepared' }, 201);
    if (path.endsWith('/resume')) return json(f.state.outcome);
    return json({ ...f.server.meta, last_sequence: 1 });
  };
  f.agent.attach(ID, 'customer');
  await vi.waitFor(() =>
    expect(f.agent.getState().privateAvailable).toBe(true),
  );
  await f.agent.ask('parking', {
    booking_references: ['invalid space'],
    transaction_references: [],
  });
  expect(posts(f)).toHaveLength(0);
  await f.agent.ask('parking', refs);
  expect(JSON.parse(String(posts(f)[0]![1]?.body))).toMatchObject({
    purpose: 'assist',
    ...refs,
  });
  expect(posts(f)).toHaveLength(2);
});

it('forget erases output and command while blocking duplicate execution for the same question', async () => {
  const f = await setup();
  f.state.failStart = true;
  await f.agent.ask('general');
  f.agent.forget(ID);
  expect(f.agent.getState()).toMatchObject({
    phase: 'idle',
    outcome: null,
    error: null,
  });
  f.agent.detach();
  f.agent.attach(ID, 'again');
  expect(f.agent.canRecover()).toBe(false);
  expect(f.agent.canAsk()).toBe(false);
  await f.agent.recoverPrepared();
  await f.agent.ask('general');
  expect(posts(f)).toHaveLength(1);
  f.state.basis = { ...f.state.basis, messageId: ID };
  expect(f.agent.canAsk()).toBe(true);
});
it('private command and output cannot be recovered after leaving the route', async () => {
  const f = await setup(true);
  await vi.waitFor(() =>
    expect(f.agent.getState().privateAvailable).toBe(true),
  );
  await f.agent.ask('parking', {
    booking_references: ['BOOK-1'],
    transaction_references: [],
  });
  expect(f.agent.getState().outcome).not.toBeNull();
  f.agent.detach();
  f.agent.attach(ID, 'new-route');
  expect(f.agent.getState().outcome).toBeNull();
  expect(f.agent.canRecover()).toBe(false);
  expect(f.agent.canAsk()).toBe(false);
  await f.agent.recoverPrepared();
  expect(posts(f)).toHaveLength(2);
});
it('a stale enabled capability rejected by the server disables private input without unknown-start recovery', async () => {
  const f = await setup(true);
  await vi.waitFor(() =>
    expect(f.agent.getState().privateAvailable).toBe(true),
  );
  const original = f.server.override!;
  f.server.override = (path, init) =>
    path.endsWith('/agent-runs')
      ? Promise.resolve(error(503, 'AGENT_PRIVATE_UNAVAILABLE'))
      : original(path, init);
  await f.agent.ask('parking', {
    booking_references: ['BOOK-1'],
    transaction_references: [],
  });
  expect(f.agent.getState()).toMatchObject({
    phase: 'unavailable',
    privateAvailable: false,
    outcome: null,
  });
  expect(f.agent.canRecover()).toBe(false);
  expect(posts(f)).toHaveLength(1);
});
const posts = (f: Awaited<ReturnType<typeof setup>>) =>
  f.fetcher.mock.calls.filter(([, init]) => init?.method === 'POST');
it('source authorization outage purges a retained command and never retries its start', async () => {
  const f = await setup();
  const original = f.server.override!;
  f.server.override = (path, init) =>
    path.endsWith('/agent-runs')
      ? Promise.resolve(error(503, 'SOURCE_UNAVAILABLE'))
      : original(path, init);
  await f.agent.ask('general');
  expect(f.agent.getState()).toMatchObject({
    outcome: null,
    error: null,
    phase: 'idle',
  });
  expect(f.agent.canRecover()).toBe(false);
  expect(f.agent.canAsk()).toBe(false);
  await f.agent.recoverPrepared();
  expect(posts(f)).toHaveLength(1);
});
it('mount/attach does not start a run; one explicit question sends exactly start+resume with public input', async () => {
  const f = await setup();
  expect(posts(f)).toHaveLength(0);
  await f.agent.ask('parking');
  expect(posts(f)).toHaveLength(2);
  const command = JSON.parse(String(posts(f)[0]![1]?.body));
  expect(command).toMatchObject({
    purpose: 'answer',
    message_id: OTHER,
    service_type: 'parking',
  });
  expect(Object.keys(command).sort()).toEqual([
    'message_id',
    'purpose',
    'request_key',
    'service_type',
  ]);
  expect(f.agent.getState()).toMatchObject({
    phase: 'published',
    outcome: { message_id: ID },
  });
  expect(f.state.held).toBe(false);
  expect(f.refresh).toHaveBeenCalledTimes(1);
  await f.agent.ask('lounge');
  expect(posts(f)).toHaveLength(2);
});
it.each(['discarded', 'review', 'draft'] as const)(
  'handles %s without fabricating an event, confirming a case or sending email',
  async (phase) => {
    const f = await setup();
    f.state.outcome =
      phase === 'draft'
        ? { run_id: RUN, state: phase, draft_id: ID }
        : { run_id: RUN, state: phase, reason: 'REVIEW_REQUIRED' };
    await f.agent.ask('general');
    expect(f.agent.getState().phase).toBe(phase);
    expect(posts(f)).toHaveLength(2);
    expect(f.refresh).not.toHaveBeenCalled();
  },
);
it('unknown start recovers the identical key/category explicitly; prepared recovery does not run a model', async () => {
  const f = await setup();
  f.state.failStart = true;
  await f.agent.ask('parking');
  expect(f.agent.getState().phase).toBe('unknown-start');
  const original = posts(f)[0]![1]?.body;
  await f.agent.ask('lounge');
  expect(posts(f)).toHaveLength(1);
  f.state.failStart = false;
  await f.agent.recoverPrepared();
  expect(posts(f)).toHaveLength(2);
  expect(posts(f)[1]![1]?.body).toBe(original);
  expect(f.agent.getState().phase).toBe('prepared');
  await f.agent.continuePrepared();
  expect(posts(f)).toHaveLength(3);
  expect(String(posts(f)[2]![0])).toBe(`/api/v1/agent-runs/${RUN}/resume`);
});
it('unknown resume preserves the pointer across route remount but neither refresh nor recovery replays POST', async () => {
  const f = await setup();
  f.state.failAnswer = true;
  await f.agent.ask('general');
  expect(f.agent.getState().phase).toBe('unknown-answer');
  f.agent.detach();
  f.agent.attach(ID, 'route-b');
  expect(f.agent.getState().phase).toBe('unknown-answer');
  f.agent.refresh();
  await f.agent.recoverPrepared();
  await f.agent.continuePrepared();
  await f.agent.ask('general');
  expect(posts(f)).toHaveLength(2);
  expect(f.refresh).toHaveBeenCalledTimes(1);
});
it('unknown answer can read a durable receipt explicitly without another POST or model resume', async () => {
  const f = await setup();
  f.state.failAnswer = true;
  await f.agent.ask('general');
  const original = f.server.override!;
  f.server.override = (path, init) =>
    path.endsWith('/receipt')
      ? Promise.resolve(json({ run_id: RUN, state: 'unresolved' }))
      : original(path, init);
  await f.agent.checkOutcome();
  expect(f.agent.getState().phase).toBe('unknown-answer');
  f.server.override = (path, init) =>
    path.endsWith('/receipt')
      ? Promise.resolve(json(f.state.outcome))
      : original(path, init);
  await f.agent.checkOutcome();
  expect(f.agent.getState().phase).toBe('published');
  expect(posts(f)).toHaveLength(2);
  expect(f.refresh).toHaveBeenCalledTimes(1);
});
it('denied receipt destroys the retained command and a late receipt cannot restore it', async () => {
  const f = await setup();
  f.state.failAnswer = true;
  await f.agent.ask('general');
  const original = f.server.override!;
  f.server.override = (path, init) =>
    path.endsWith('/receipt')
      ? Promise.resolve(error(404, 'RESOURCE_NOT_FOUND'))
      : original(path, init);
  await f.agent.checkOutcome();
  expect(f.agent.getState().outcome).toBeNull();
  expect(f.agent.canRecover()).toBe(false);
  expect(posts(f)).toHaveLength(2);
});
it.each(['route', 'mode', 'epoch', 'language', 'auth'] as const)(
  'discards late work after %s changes without replay',
  async (change) => {
    const f = await setup();
    let resolve!: (v: Response) => void;
    const original = f.server.override!;
    f.server.override = (path, init) =>
      path.endsWith('/resume')
        ? new Promise<Response>((r) => {
            resolve = r;
          })
        : original(path, init);
    const asking = f.agent.ask('general');
    for (let n = 0; n < 30 && !resolve; n++)
      await new Promise<void>((r) => queueMicrotask(r));
    expect(resolve).toBeTypeOf('function');
    if (change === 'route') f.agent.detach();
    else if (change === 'auth') await f.auth.recheck();
    else {
      f.state.compatible = false;
      f.agent.reconcile();
    }
    resolve(
      json({ run_id: RUN, state: 'published', message_id: ID, sequence: 2 }),
    );
    await asking;
    expect(f.agent.getState().outcome).toBeNull();
    expect(posts(f)).toHaveLength(2);
    expect(f.state.held).toBe(false);
  },
);
it('changed fingerprint prevents same-key recovery; a new durable customer question can request its own answer', async () => {
  const f = await setup();
  f.state.failStart = true;
  await f.agent.ask('parking');
  f.state.basis = { ...f.state.basis, version: 2, epoch: 1, language: 'id' };
  expect(f.agent.canRecover()).toBe(false);
  await f.agent.recoverPrepared();
  expect(posts(f)).toHaveLength(1);
  f.state.basis = { ...f.state.basis, messageId: ID };
  expect(f.agent.canAsk()).toBe(true);
});
it('dependency503 is a fallback with no invented answer or automatic retry', async () => {
  const f = await setup();
  const original = f.server.override!;
  f.server.override = (path, init) =>
    path.endsWith('/agent-runs')
      ? Promise.resolve(error(503, 'AGENT_KNOWLEDGE_UNAVAILABLE'))
      : original(path, init);
  await f.agent.ask('general');
  expect(f.agent.getState()).toMatchObject({
    phase: 'unavailable',
    outcome: null,
  });
  expect(posts(f)).toHaveLength(1);
});
it('definite rejection is distinguished from unknown submission and does not automatically retry', async () => {
  const f = await setup();
  const original = f.server.override!;
  f.server.override = (path, init) =>
    path.endsWith('/agent-runs')
      ? Promise.resolve(error(422, 'AGENT_INVALID_INPUT'))
      : original(path, init);
  await f.agent.ask('general');
  expect(f.agent.getState()).toMatchObject({
    phase: 'review',
    error: { outcome: 'rejected', status: 422 },
  });
  await f.agent.recoverPrepared();
  expect(posts(f)).toHaveLength(1);
});
it('revalidates metadata before POST, and rejects an outcome for another run', async () => {
  const f = await setup();
  f.state.outcome = {
    run_id: OTHER,
    state: 'published',
    message_id: ID,
    sequence: 2,
  };
  await f.agent.ask('general');
  expect(f.agent.getState()).toMatchObject({
    phase: 'unknown-answer',
    outcome: null,
    error: { kind: 'invalid-response' },
  });
  expect(
    f.fetcher.mock.calls.filter(
      ([path]) => path === '/api/v1/conversations/' + ID,
    ),
  ).toHaveLength(2);
});
it('stale authorized metadata prevents starting work at all', async () => {
  const f = await setup();
  const original = f.server.override!;
  f.server.override = (path, init) =>
    path === '/api/v1/conversations/' + ID
      ? Promise.resolve(
          json({
            conversation_id: ID,
            language: 'en',
            version: 2,
            generation_epoch: 0,
            last_sequence: 1,
          }),
        )
      : original(path, init);
  await f.agent.ask('general');
  expect(posts(f)).toHaveLength(0);
  expect(f.agent.getState().phase).toBe('review');
});
it('resume uses its own120s timeout and never replays timed-out model work', async () => {
  vi.useFakeTimers();
  const f = await setup();
  const original = f.server.override!;
  f.server.override = (path, init) =>
    path.endsWith('/resume')
      ? new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener(
            'abort',
            () => reject(Error('aborted')),
            { once: true },
          );
        })
      : original(path, init);
  const answer = f.agent.ask('general');
  await vi.advanceTimersByTimeAsync(15001);
  expect(posts(f)).toHaveLength(2);
  expect(f.agent.getState().phase).toBe('answering');
  await vi.advanceTimersByTimeAsync(105000);
  await answer;
  expect(f.agent.getState()).toMatchObject({
    phase: 'unknown-answer',
    error: { kind: 'timeout', outcome: 'unknown' },
  });
  expect(posts(f)).toHaveLength(2);
  await f.agent.continuePrepared();
  expect(posts(f)).toHaveLength(2);
});
it('429 waits across route remount and explicit recovery keeps the same original key', async () => {
  vi.useFakeTimers();
  const f = await setup();
  const original = f.server.override!;
  f.server.override = (path, init) => {
    if (!path.endsWith('/agent-runs')) return original(path, init);
    const response = error(429, 'RATE_LIMITED');
    response.headers.set('Retry-After', '20');
    return Promise.resolve(response);
  };
  await f.agent.ask('parking');
  const body = posts(f)[0]![1]?.body;
  f.agent.attach(ID, 'route-b');
  await f.agent.recoverPrepared();
  expect(posts(f)).toHaveLength(1);
  await vi.advanceTimersByTimeAsync(20001);
  expect(posts(f)).toHaveLength(1);
  f.server.override = original;
  await f.agent.recoverPrepared();
  expect(posts(f)).toHaveLength(2);
  expect(posts(f)[1]![1]?.body).toBe(body);
  expect(f.agent.getState().phase).toBe('prepared');
});
