import { describe, expect, it } from 'vitest';
import { AuthCoordinator } from '../../src/app/auth/coordinator';
import { ChatStore } from '../../src/features/chat/store';

const id = '11111111-1111-4111-8111-111111111111';
const path = '/conversations/' + id;
const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
function fixture() {
  const server = {
    getStatus: 200,
    appendStatus: 201,
    revokeAfterAppend: false,
    unknownCreate: false,
    languageStatus: 200,
  };
  const posts: { path: string; body: unknown }[] = [];
  const meta = {
    conversation_id: id,
    language: 'en',
    version: 1,
    generation_epoch: 0,
    last_sequence: 0,
  };
  const auth = new AuthCoordinator({
    readProof: () => 'p'.repeat(43),
    fetch: async (url, init) => {
      const route = String(url);
      if (route === '/api/v1/me')
        return json({ session_kind: 'anonymous', principal_id: null });
      if (init?.method === 'PATCH') return json(meta, server.languageStatus);
      if (init?.method === 'POST') {
        posts.push({ path: route, body: JSON.parse(String(init.body)) });
        if (route === '/api/v1/conversations') {
          if (server.unknownCreate)
            throw new Error('synthetic lost create response');
          return json(meta, 201);
        }
        if (server.appendStatus !== 201) return json({}, server.appendStatus);
        if (server.revokeAfterAppend) server.getStatus = 404;
        return json(
          { message_id: id, sequence: 1, processing_state: 'received' },
          201,
        );
      }
      return json(meta, server.getStatus);
    },
  });
  const store = new ChatStore(auth, 'qa');
  const off = store.connect();
  return {
    server,
    posts,
    auth,
    store,
    dispose() {
      off();
      store.dispose();
      auth.dispose();
    },
  };
}

describe('independent chat regressions', () => {
  it('a definitively rejected403 does not become an unknown committed-message claim after authorized recheck', async () => {
    const f = fixture();
    try {
      await f.auth.start();
      await f.store.open(id);
      f.store.input({ body: 'still authorized draft', service: '' });
      f.server.appendStatus = 403;
      await f.store.send();
      await f.store.open(id);
      expect(f.posts).toHaveLength(1);
      expect(f.store.getState().input.body).toBe('still authorized draft');
      expect(f.store.getState().unresolved).toBe(false);
      expect(f.store.getState().messages).toEqual([]);
      expect(f.store.getState().reviewNeeded).toBe(true);
      f.server.appendStatus = 201;
      await f.store.send();
      expect(f.posts).toHaveLength(2);
      const original = f.posts[0]!.body as {
        client_message_id: string;
        body: string;
      };
      const reviewed = f.posts[1]!.body as {
        client_message_id: string;
        body: string;
      };
      expect(reviewed.client_message_id).not.toBe(original.client_message_id);
      expect(reviewed.body).toBe(original.body);
      expect(f.store.getState().messages[0]?.state).toBe('received');
      expect(f.store.getState().reviewNeeded).toBe(false);
    } finally {
      f.dispose();
    }
  });
  it('reset during an in-flight create keeps outcome unknown and blocks a replacement', async () => {
    let finish!: (response: Response) => void;
    let posts = 0;
    const auth = new AuthCoordinator({
      readProof: () => 'p'.repeat(43),
      fetch: async (url) => {
        if (String(url) === '/api/v1/me')
          return json({ session_kind: 'anonymous', principal_id: null });
        posts++;
        return new Promise<Response>((resolve) => {
          finish = resolve;
        });
      },
    });
    const store = new ChatStore(auth, 'qa');
    const off = store.connect();
    try {
      await auth.start();
      const pending = store.create('en');
      await auth.recheck();
      finish(
        json(
          {
            conversation_id: id,
            language: 'en',
            version: 1,
            generation_epoch: 0,
            last_sequence: 0,
          },
          201,
        ),
      );
      await pending;
      store.newEntry();
      await store.create('en');
      expect(posts).toBe(1);
      expect(store.getState().phase).toBe('unknown-create');
      expect(store.getState().meta).toBeNull();
    } finally {
      off();
      store.dispose();
      auth.dispose();
    }
  });

  it('discard aborts metadata and a late result cannot restore the prior selection', async () => {
    let finish!: (response: Response) => void;
    let readSignal: AbortSignal | null | undefined;
    const auth = new AuthCoordinator({
      readProof: () => 'p'.repeat(43),
      fetch: async (url, init) => {
        if (String(url) === '/api/v1/me')
          return json({ session_kind: 'anonymous', principal_id: null });
        readSignal = init?.signal;
        return new Promise<Response>((resolve) => {
          finish = resolve;
        });
      },
    });
    const store = new ChatStore(auth, 'qa');
    const off = store.connect();
    try {
      await auth.start();
      const pending = store.open(id);
      store.discard();
      expect(readSignal?.aborted).toBe(true);
      finish(
        json({
          conversation_id: id,
          language: 'en',
          version: 1,
          generation_epoch: 0,
          last_sequence: 0,
        }),
      );
      await pending;
      expect(store.getState()).toMatchObject({
        phase: 'idle',
        meta: null,
        input: { body: '', service: '' },
        messages: [],
      });
    } finally {
      off();
      store.dispose();
      auth.dispose();
    }
  });
  it('language mutation404 cannot reintroduce the revoked retained input', async () => {
    const f = fixture();
    try {
      await f.auth.start();
      await f.store.open(id);
      f.store.input({ body: 'private language draft', service: '' });
      f.server.languageStatus = 404;
      await f.store.language('id');
      expect(f.store.getState().input.body).toBe('');
      expect(f.store.getState().meta).toBeNull();
      expect(f.auth.retained(path, f.auth.capture())).toBeUndefined();
      await f.store.open(id);
      expect(f.store.getState().input.body).toBe('');
    } finally {
      f.dispose();
    }
  });
  it.each(['append-404', 'metadata-404'] as const)(
    '%s clears resource input and local messages',
    async (mode) => {
      const f = fixture();
      try {
        await f.auth.start();
        await f.store.open(id);
        f.store.input({ body: 'private report', service: '' });
        if (mode === 'append-404') f.server.appendStatus = 404;
        else f.server.revokeAfterAppend = true;
        await f.store.send();
        expect(f.store.getState().input.body).toBe('');
        expect(f.store.getState().messages).toEqual([]);
        expect(f.store.getState().meta).toBeNull();
        expect(f.auth.retained(path, f.auth.capture())).toBeUndefined();
      } finally {
        f.dispose();
      }
    },
  );

  it('explicit discard cannot resurrect the input on fresh authorized reopen', async () => {
    const f = fixture();
    try {
      await f.auth.start();
      await f.store.open(id);
      f.store.input({ body: 'discard this input', service: 'parking' });
      f.store.discard();
      await f.store.open(id);
      expect(f.store.getState().input).toEqual({ body: '', service: '' });
    } finally {
      f.dispose();
    }
  });

  it('same-identity session recheck does not erase an unknown-create safeguard', async () => {
    const f = fixture();
    try {
      await f.auth.start();
      f.server.unknownCreate = true;
      await f.store.create('en');
      expect(f.store.getState().phase).toBe('unknown-create');
      await f.auth.recheck();
      f.store.newEntry();
      await f.store.create('en');
      expect(f.posts).toHaveLength(1);
      expect(f.store.getState().phase).toBe('unknown-create');
    } finally {
      f.dispose();
    }
  });

  it('retry uses exact message UUID/body and never adds another local message', async () => {
    const f = fixture();
    try {
      await f.auth.start();
      await f.store.open(id);
      f.store.input({ body: 'same original words', service: 'parking' });
      f.server.appendStatus = 503;
      await f.store.send();
      expect(f.store.getState().messages[0]?.state).toBe('unknown');
      f.server.appendStatus = 201;
      await f.store.send(true);
      expect(f.posts[0]).toEqual(f.posts[1]);
      expect(f.store.getState().messages).toHaveLength(1);
      expect(f.store.getState().messages[0]?.state).toBe('received');
    } finally {
      f.dispose();
    }
  });
});
