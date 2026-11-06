import { afterEach, expect, it } from 'vitest';
import { fixture, ID, OTHER, json, error } from './fixture';
import {
  decodeConversation,
  decodeReceipt,
  messageSchema,
} from '../../src/features/chat/contracts';
const owned: ReturnType<typeof fixture>[] = [];
const setup = () => {
  const f = fixture();
  owned.push(f);
  return f;
};
afterEach(() => owned.splice(0).forEach((f) => f.dispose()));
it('never creates on construction/open/remount and deduplicates explicit start clicks', async () => {
  const f = setup();
  await f.auth.start();
  expect(f.server.creates).toBe(0);
  await Promise.all([f.store.create('en'), f.store.create('en')]);
  expect(f.server.creates).toBe(1);
  await Promise.all([f.store.open(ID), f.store.open(ID)]);
  expect(f.server.creates).toBe(1);
});
it('binds the unavailable target to its owner and ignores a late response from another route', async () => {
  const f = setup();
  await f.auth.start();
  let finishA!: (value: Response) => void;
  f.server.override = async (url) => {
    if (url === '/api/v1/conversations/' + ID)
      return new Promise<Response>((resolve) => {
        finishA = resolve;
      });
    return error(404, 'RESOURCE_NOT_FOUND');
  };
  const pendingA = f.store.open(ID);
  await Promise.resolve();
  await f.store.open(OTHER);
  expect(f.store.getState().phase).toBe('unavailable');
  expect(f.store.requestedConversationId).toBe(OTHER);
  finishA(json(f.server.meta));
  await pendingA;
  expect(f.store.getState().meta).toBeNull();
  expect(f.store.requestedConversationId).toBe(OTHER);
  f.store.newEntry();
  expect(f.store.requestedConversationId).toBeNull();
  f.server.override = null;
  await f.store.create('en');
  expect(f.store.requestedConversationId).toBe(ID);
  f.server.proof = 'q'.repeat(43);
  expect(() => f.auth.capture()).toThrow();
  expect(f.store.requestedConversationId).toBeNull();
});
it('create response loss is unknown and cannot implicitly create a replacement', async () => {
  const f = setup();
  await f.auth.start();
  f.server.loseCreate = true;
  await f.store.create('en');
  expect(f.store.getState().phase).toBe('unknown-create');
  await f.store.create('en');
  f.store.newEntry();
  await f.store.create('en');
  expect(f.server.creates).toBe(1);
});
it('append retry preserves UUID/body and one received local message after server committed but response lost', async () => {
  const f = setup();
  await f.auth.start();
  await f.store.create('en');
  f.store.input({ body: 'My original story', service: 'parking' });
  f.server.loseAppend = true;
  await f.store.send();
  const first = f.store.getState().messages[0]!;
  expect(first.state).toBe('unknown');
  expect(f.server.messages.size).toBe(1);
  f.server.loseAppend = false;
  await f.store.send(true);
  const last = f.store.getState();
  expect(last.messages).toHaveLength(1);
  expect(last.messages[0]).toMatchObject({
    clientId: first.clientId,
    body: first.body,
    state: 'received',
  });
  expect(f.server.messages.size).toBe(1);
  expect(last.meta?.version).toBe(2);
  expect(last.input.body).toBe('');
});
it('message conflict never silently replaces key or retries automatically', async () => {
  const f = setup();
  await f.auth.start();
  await f.store.create('en');
  f.store.input({ body: 'Original', service: '' });
  f.server.override = async (path) =>
    path.endsWith('/messages')
      ? error(409, 'MESSAGE_CONFLICT')
      : json(f.server.meta);
  await f.store.send();
  const count = f.fetcher.mock.calls.length;
  await f.store.send(true);
  expect(f.fetcher.mock.calls).toHaveLength(count);
  expect(f.store.getState().messages[0]?.state).toBe('rejected');
});
it('language uses fresh conversation version and preserves original story/messages without rewriting them', async () => {
  const f = setup();
  await f.auth.start();
  await f.store.create('en');
  f.store.input({ body: 'Original English text', service: '' });
  await f.store.send();
  f.store.input({ body: 'Unsubmitted original', service: 'lounge' });
  await f.store.language('id');
  expect(f.server.patches).toBe(1);
  expect(f.store.getState().meta).toMatchObject({ version: 3, language: 'id' });
  expect(f.store.getState().input.body).toBe('Unsubmitted original');
  expect(f.store.getState().messages[0]).toMatchObject({
    language: 'en',
    body: 'Original English text',
  });
  const request = f.fetcher.mock.calls.find(([url]) =>
    String(url).endsWith('/language'),
  )!;
  expect(JSON.parse(String(request[1]?.body))).toEqual({
    language: 'id',
    expected_version: 2,
  });
});
it('language conflict stops, explicit metadata check refreshes version without replaying PATCH', async () => {
  const f = setup();
  await f.auth.start();
  await f.store.create('en');
  f.server.conflictLanguage = true;
  await f.store.language('id');
  expect(f.store.getState().phase).toBe('unavailable');
  await f.store.open(ID);
  expect(f.server.patches).toBe(1);
  expect(f.store.getState().meta?.language).toBe('en');
});
it('fresh authorized metadata restores input on same-boundary recheck, never pending commands', async () => {
  const f = setup();
  await f.auth.start();
  await f.store.create('en');
  f.store.input({ body: 'Still authorized', service: '' });
  await f.auth.recheck();
  expect(f.store.getState().input.body).toBe('');
  await f.store.open(ID);
  expect(f.store.getState().input.body).toBe('Still authorized');
  f.server.loseAppend = true;
  await f.store.send();
  await f.auth.recheck();
  await f.store.open(ID);
  expect(f.store.getState().unresolved).toBe(true);
  expect(f.store.getState().messages).toHaveLength(0);
  const count = f.server.posts;
  await f.store.send(true);
  await f.store.send();
  expect(f.server.posts).toBe(count);
});
it('new actor cannot inherit story or pending command from previous session', async () => {
  const f = setup();
  await f.auth.start();
  await f.store.create('en');
  f.store.input({ body: 'ACCOUNT A', service: '' });
  f.server.proof = 'q'.repeat(43);
  f.server.kind = 'customer';
  f.server.principal = OTHER;
  await f.auth.recheck();
  await f.store.open(ID);
  expect(f.store.getState().input.body).toBe('');
  expect(f.store.getState().messages).toHaveLength(0);
});
it('revoked resource discards private text, reload metadata never pretends to return history', async () => {
  const f = setup();
  await f.auth.start();
  await f.store.create('en');
  f.store.input({ body: 'PRIVATE', service: '' });
  await f.store.send();
  f.store.dispose();
  await f.store.open(ID);
  expect(f.store.getState().messages).toHaveLength(1);
  f.server.override = async () => error(404, 'RESOURCE_NOT_FOUND');
  await f.store.open(ID);
  expect(f.store.getState()).toMatchObject({
    phase: 'unavailable',
    meta: null,
    input: { body: '', service: '' },
    messages: [],
  });
});
it('runtime DTOs reject malformed/versionless metadata and fake AI receipt; validates nonempty text', () => {
  expect(() =>
    decodeConversation({
      conversation_id: ID,
      language: 'en',
      version: 0,
      generation_epoch: 0,
      last_sequence: 0,
    }),
  ).toThrow();
  expect(() =>
    decodeReceipt({
      message_id: ID,
      sequence: 1,
      processing_state: 'answered',
    }),
  ).toThrow();
  expect(messageSchema.safeParse({ body: '   ', service: '' }).success).toBe(
    false,
  );
  expect(messageSchema.safeParse({ body: 'a\0b', service: '' }).success).toBe(
    false,
  );
});
