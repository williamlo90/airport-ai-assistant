import { afterEach, expect, it, vi } from 'vitest';
import { STAFF, caseId } from '../../src/dev/staff-fixtures';
import { StaffCommunicationStore } from '../../src/features/staff/communication-store';
import {
  communicationFixture,
  enableConversation,
} from './communication-fixture';
const owned: (() => void)[] = [];
afterEach(() => {
  owned.splice(0).forEach((off) => off());
  vi.useRealTimers();
});
async function fixture() {
  const f = await communicationFixture();
  owned.push(f.dispose);
  return f;
}

it('mount only reads on explicit load and never sends before a human action', async () => {
  const f = await fixture(),
    before = f.server.requests.length;
  expect(f.store.getState().history).toBeNull();
  await f.store.load();
  expect(f.store.canReply('email')).toBe(true);
  expect(f.server.requests.slice(before).every((r) => r.method === 'GET')).toBe(
    true,
  );
  f.store.edit('Please wait for our email.');
  await f.store.send();
  expect(f.state.sends).toBe(1);
  expect(f.store.getState()).toMatchObject({
    body: '',
    pending: null,
    notice: 'saved',
  });
});
it('lost send is recovered by GET only; missing receipt stays unknown and blocks another POST', async () => {
  const f = await fixture();
  await f.store.load();
  f.state.lostSend = true;
  f.store.edit('Please check this request.');
  await f.store.send();
  expect(f.store.getState().phase).toBe('unknown');
  f.state.receiptMissing = true;
  await f.store.recover();
  await f.store.send();
  expect(f.store.getState()).toMatchObject({
    phase: 'unknown',
    body: '',
    draft: null,
  });
  expect(f.state.sends).toBe(1);
  f.state.receiptMissing = false;
  await f.store.recover();
  expect(f.store.getState().pending).toBeNull();
  expect(f.state.sends).toBe(1);
});
it('translation previews do not send; edited review requires explicit approval', async () => {
  const f = await fixture();
  await f.store.load();
  f.store.configure('id', 'email');
  f.store.edit('Mohon tunggu.');
  await f.store.translate('en');
  expect(f.state.prepares).toBe(1);
  expect(f.state.approvals).toBe(0);
  expect(f.state.sends).toBe(0);
  expect(f.store.getState().draft?.original_body).toBe('Mohon tunggu.');
  f.store.review('We will check and follow up by email.');
  await f.store.approve();
  expect(f.state.approvals).toBe(1);
  expect(f.state.draft?.reviewed_body).toBe(
    'We will check and follow up by email.',
  );
  expect(f.store.getState().draft).toBeNull();
});
it('lost translation preparation and approval responses recover without another model or approval POST', async () => {
  const f = await fixture();
  await f.store.load();
  f.state.lostPrepare = true;
  f.store.configure('id', 'email');
  f.store.edit('Mohon tunggu.');
  await f.store.translate('en');
  expect(f.store.getState().phase).toBe('unknown');
  await f.store.recover();
  expect(f.store.getState().draft?.state).toBe('ready');
  expect(f.state.prepares).toBe(1);
  f.state.lostApproval = true;
  await f.store.approve();
  await f.store.recover();
  expect(f.state.prepares).toBe(1);
  expect(f.state.approvals).toBe(1);
  expect(f.store.getState().pending).toBeNull();
});
it('late results from another selected case cannot republish private content', async () => {
  const f = await fixture();
  await f.store.load();
  let resolve!: (r: Response) => void;
  const original = f.server.custom!;
  f.server.custom = (path, init) =>
    path.endsWith('/email-messages')
      ? new Promise<Response>((r) => {
          resolve = r;
        })
      : original(path, init);
  f.store.edit('Private case A reply');
  const sending = f.store.send();
  await f.queue.openCase(caseId(8));
  resolve(f.json(f.receipt, 201));
  await sending;
  expect(f.store.getState()).toMatchObject({
    available: false,
    body: '',
    draft: null,
    history: null,
  });
});
it('source unavailable removes source-bearing history, drafts and composer immediately', async () => {
  const f = await fixture();
  await f.store.load();
  f.store.configure('id', 'email');
  f.store.edit('Mohon tunggu.');
  await f.store.translate('en');
  f.server.custom = () =>
    Promise.resolve(
      f.json(
        {
          error: {
            code: 'SOURCE_UNAVAILABLE',
            message: 'Synthetic outage',
            request_id: STAFF,
            retryable: true,
          },
        },
        503,
      ),
    );
  await f.store.load();
  expect(f.store.getState()).toMatchObject({
    body: '',
    draft: null,
    reviewed: '',
    history: null,
  });
});
it('admin-only roles cannot read or send even if the transport would allow it', async () => {
  const f = await fixture();
  f.server.roles = ['admin'];
  await f.auth.recheck();
  const before = f.server.requests.length;
  await f.store.load();
  f.store.edit('Not permitted.');
  await f.store.send();
  expect(f.server.requests).toHaveLength(before);
  expect(f.store.canReply()).toBe(false);
});
it('lost accept uses current GET state, then chat reply uses refreshed case/version/epoch', async () => {
  const f = await fixture(),
    chat = await enableConversation(f, true);
  await f.store.load();
  expect(f.store.canReply('chat')).toBe(false);
  await f.store.accept();
  expect(f.store.getState().phase).toBe('unknown');
  await f.store.recover();
  expect(chat.accepts).toBe(1);
  expect(f.store.getState().pending).toBeNull();
  expect(f.store.canReply('chat')).toBe(true);
  f.store.configure('en', 'chat');
  f.store.edit('I will check your request.');
  await f.store.send();
  expect(chat.replies).toBe(1);
  expect(chat.command).toMatchObject({
    expected_version: 3,
    expected_conversation_version: 3,
    expected_epoch: 2,
    channel: 'chat',
  });
});

it.each(['send', 'translate', 'approve'] as const)(
  'postcommit SOURCE_UNAVAILABLE keeps safe recovery IDs for %s',
  async (kind) => {
    const f = await fixture();
    await f.store.load();
    f.store.configure('id', 'email');
    f.store.edit('Mohon tunggu.');
    if (kind === 'approve') await f.store.translate('en');
    const original = f.server.custom!;
    f.server.custom = (path, init) => {
      const target =
        kind === 'send'
          ? path.endsWith('/email-messages')
          : kind === 'translate'
            ? path.endsWith('/email-translation-drafts')
            : path.endsWith('/send');
      const result = original(path, init);
      if (!target) return result;
      return Promise.resolve(result).then(() =>
        f.json(
          {
            error: {
              code: 'SOURCE_UNAVAILABLE',
              message: 'Synthetic postcommit failure',
              request_id: STAFF,
              retryable: true,
            },
          },
          503,
        ),
      );
    };
    if (kind === 'send') await f.store.send();
    else if (kind === 'translate') await f.store.translate('en');
    else await f.store.approve();
    expect(f.store.getState()).toMatchObject({
      body: '',
      reviewed: '',
      draft: null,
      history: null,
    });
    await f.queue.openCase(caseId(4), true);
    expect(f.store.getState().pending?.kind).toBe(kind);
    expect(f.store.getState().body).toBe('');
    const counts = [f.state.sends, f.state.prepares, f.state.approvals];
    await f.store.recover();
    expect(f.store.getState().pending).toBeNull();
    expect([f.state.sends, f.state.prepares, f.state.approvals]).toEqual(
      counts,
    );
    if (kind === 'translate')
      expect(f.store.getState()).toMatchObject({
        language: 'id',
        channel: 'email',
        draft: { state: 'ready' },
      });
  },
);

it('navigation discard removes text but keeps an unknown operation recoverable after remount', async () => {
  const f = await fixture();
  await f.store.load();
  f.state.lostSend = true;
  f.store.edit('Private unknown reply');
  await f.store.send();
  f.store.discard();
  expect(f.store.getState()).toMatchObject({
    body: '',
    phase: 'unknown',
    pending: { kind: 'send' },
  });
  const remounted = new StaffCommunicationStore(f.queue, caseId(4)),
    off = remounted.connect();
  try {
    expect(remounted.getState().pending?.kind).toBe('send');
    await remounted.recover();
    expect(remounted.getState().pending).toBeNull();
    expect(f.state.sends).toBe(1);
  } finally {
    off();
  }
});

it('postcommit source failure on a chat reply restores the actual chat channel for its saved status', async () => {
  const f = await fixture(),
    chat = await enableConversation(f);
  await f.store.load();
  await f.store.accept();
  f.store.configure('en', 'chat');
  f.store.edit('Private live chat reply.');
  const original = f.server.custom!;
  f.server.custom = (path, init) => {
    if (path.includes('/message-receipts/'))
      return Promise.resolve(
        f.json({
          ...f.receipt,
          conversation_id: chat.chat.conversation_id,
          channel: 'chat',
        }),
      );
    const result = original(path, init);
    if (!path.endsWith('/messages') || init?.method !== 'POST') return result;
    return Promise.resolve(result).then(() =>
      f.json(
        {
          error: {
            code: 'SOURCE_UNAVAILABLE',
            message: 'Synthetic postcommit error',
            request_id: STAFF,
            retryable: true,
          },
        },
        503,
      ),
    );
  };
  await f.store.send();
  await f.queue.openCase(caseId(4), true);
  expect(f.store.getState()).toMatchObject({
    pending: { kind: 'send', channel: 'chat' },
    channel: 'chat',
    body: '',
  });
  await f.store.recover();
  expect(f.store.getState()).toMatchObject({
    pending: null,
    channel: 'chat',
    notice: 'saved',
  });
  expect(chat.replies).toBe(1);
});

it.each(['owner', 'membership'] as const)(
  'reset or changed %s cannot restore another boundary recovery marker',
  async (change) => {
    const f = await fixture();
    await f.store.load();
    f.state.lostSend = true;
    f.store.edit('Unknown reply in the previous boundary');
    await f.store.send();
    expect(f.store.getState().pending?.kind).toBe('send');
    if (change === 'owner')
      f.server.principal = '22222222-2222-4222-8222-222222222222';
    else f.server.memberVersion++;
    await f.auth.recheck();
    await f.queue.ensureQueue();
    await f.queue.openCase(caseId(4), true);
    expect(f.store.getState().pending).toBeNull();
    expect(f.store.getState().body).toBe('');
  },
);
