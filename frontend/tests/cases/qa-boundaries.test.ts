import { afterEach, expect, it } from 'vitest';
import { caseFixture, DRAFT, NEXT, ID, json, error } from './fixture';
import { decodeDraft } from '../../src/features/cases/contracts';

const owned: ReturnType<typeof caseFixture>[] = [];
const make = () => {
  const f = caseFixture();
  owned.push(f);
  return f;
};
afterEach(() => owned.splice(0).forEach((f) => f.dispose()));
const deferred = () => {
  let resolve!: (response: Response) => void;
  const promise = new Promise<Response>((done) => {
    resolve = done;
  });
  return { promise, resolve };
};

it('selects a different draft during a pending PATCH and fences the previous mutation response', async () => {
  const f = make();
  await f.ready();
  await f.cases.create();
  f.cases.beginEdit();
  const delayed = deferred();
  f.previewServer.handler = (method) =>
    method === 'PATCH' ? delayed.promise : null;
  const saving = f.cases.update();
  await Promise.resolve();
  await f.cases.open(NEXT);
  delayed.resolve(json({ ...f.previewServer.draft, version: 2 }));
  await saving;
  expect(f.cases.getState().draft?.draft_id).toBe(NEXT);
  await f.cases.open(DRAFT);
  expect(f.cases.getState().uncertain?.kind).toBe('edit');
});

it('does not renew a different selection when an earlier renewal GET completes late', async () => {
  const f = make();
  await f.ready();
  await f.cases.create();
  f.previewServer.draft.state = 'expired';
  await f.cases.open(DRAFT);
  const delayed = deferred();
  f.previewServer.handler = (method, path) =>
    method === 'GET' && path.endsWith(DRAFT) ? delayed.promise : null;
  const renewal = f.cases.renew();
  await Promise.resolve();
  await f.cases.open(NEXT);
  delayed.resolve(json(f.previewServer.draft));
  const renewed = await renewal;
  expect(renewed).toBe(false);
  expect(f.cases.getState().draft?.draft_id).toBe(NEXT);
});

it('late conflict reconciliation does not close or annotate another draft editor', async () => {
  const f = make();
  await f.ready();
  await f.cases.create();
  f.cases.beginEdit();
  const delayed = deferred();
  let started!: () => void;
  const reconciling = new Promise<void>((done) => {
    started = done;
  });
  f.previewServer.handler = (method, path) => {
    if (method === 'PATCH')
      return Promise.resolve(error(409, 'VERSION_CONFLICT'));
    if (method === 'GET' && path.endsWith(DRAFT)) {
      started();
      return delayed.promise;
    }
    return null;
  };
  const saving = f.cases.update();
  await reconciling;
  await f.cases.open(NEXT);
  f.cases.beginEdit();
  f.cases.input({
    ...f.cases.getState().form,
    summary: 'Unrelated draft edit',
  });
  delayed.resolve(json(f.previewServer.draft));
  await saving;
  expect(f.cases.getState()).toMatchObject({
    draft: { draft_id: NEXT },
    editing: true,
    error: null,
    form: { summary: 'Unrelated draft edit' },
  });
});

it('definitive PATCH403 erases private edits and uncertainty before same-session recovery', async () => {
  const f = make();
  await f.ready();
  await f.cases.create();
  f.cases.beginEdit();
  f.cases.input({
    ...f.cases.getState().form,
    summary: 'Private unsaved change',
  });
  let patches = 0;
  f.previewServer.handler = (method) => {
    if (method === 'PATCH') {
      patches++;
      return Promise.resolve(error(403, 'ACTION_FORBIDDEN'));
    }
    return null;
  };
  await f.cases.update();
  expect(patches).toBe(1);
  expect(f.cases.getState().draft).toBeNull();
  expect(f.cases.getState().form.summary).toBe('');
  expect(f.cases.getState().uncertain).toBeNull();
  expect(
    f.auth.retained('case-draft:/action-drafts/' + DRAFT, f.auth.capture()),
  ).toBeUndefined();
});

it('late PATCH cannot restore private data after customer replacement', async () => {
  const f = make();
  await f.ready();
  await f.cases.create();
  f.cases.beginEdit();
  const delayed = deferred();
  f.previewServer.handler = (method) =>
    method === 'PATCH' ? delayed.promise : null;
  const saving = f.cases.update();
  await Promise.resolve();
  f.server.proof = 'z'.repeat(43);
  f.server.principal = ID;
  await f.auth.recheck();
  delayed.resolve(json({ ...f.previewServer.draft, version: 2 }));
  await saving;
  expect(f.cases.getState().draft).toBeNull();
  expect(f.cases.getState().form.contact_email).toBe('');
  expect(f.cases.queries.getQueryCache().getAll()).toHaveLength(0);
});

it('a forged successful PATCH extending the server deadline stays unknown and is not replayed', async () => {
  const f = make();
  await f.ready();
  await f.cases.create();
  const original = f.cases.getState().draft!.expires_at;
  f.cases.beginEdit();
  let patches = 0;
  f.previewServer.handler = (method) => {
    if (method !== 'PATCH') return null;
    patches++;
    return Promise.resolve(
      json({
        ...f.previewServer.draft,
        version: 2,
        expires_at: new Date(Date.parse(original) + 60000).toISOString(),
      }),
    );
  };
  await f.cases.update();
  await f.cases.update();
  expect(patches).toBe(1);
  expect(f.cases.getState().draft?.expires_at).toBe(original);
  expect(f.cases.getState().uncertain?.kind).toBe('edit');
});

it('unknown create stays latched after returning through a fresh authorized chat read', async () => {
  const f = make();
  await f.ready();
  let creates = 0;
  f.previewServer.handler = (method) => {
    if (method === 'POST') {
      creates++;
      return Promise.reject(Error('Lost receipt'));
    }
    return null;
  };
  await f.cases.create();
  await f.auth.recheck();
  await f.cases.enterIntake();
  await f.store.open(ID);
  await f.cases.prepareFromChat('booking', 'customer@example.com');
  await f.cases.create();
  expect(creates).toBe(1);
  expect(f.cases.getState().uncertain?.kind).toBe('create');
});

it('rejects array-valued DTO state rather than coercing it into an enum', () => {
  const f = make();
  expect(() =>
    decodeDraft({ ...f.previewServer.draft, state: ['pending'] }),
  ).toThrow();
});

it('rejects array-valued DTO action rather than coercing it into an enum', () => {
  const f = make();
  expect(() =>
    decodeDraft({ ...f.previewServer.draft, action_type: ['create_case'] }),
  ).toThrow();
});
