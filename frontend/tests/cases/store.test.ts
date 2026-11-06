import { afterEach, expect, it } from 'vitest';
import { caseFixture, DRAFT, NEXT, ID, OTHER, error, json } from './fixture';
import {
  decodeDraft,
  formSchema,
  toPayload,
} from '../../src/features/cases/contracts';
const owned: ReturnType<typeof caseFixture>[] = [];
const make = () => {
  const f = caseFixture();
  owned.push(f);
  return f;
};
afterEach(() => owned.splice(0).forEach((f) => f.dispose()));
it('creates only after explicit action, whitelists reports and renders server destination rather than inferred service', async () => {
  const f = make();
  await f.ready();
  expect(f.previewServer.creates).toBe(0);
  expect(f.cases.getState().form.service_type).toBe('general');
  f.cases.input({ ...f.cases.getState().form, service_type: 'parking' });
  expect(await f.cases.create()).toBe(DRAFT);
  expect(f.previewServer.creates).toBe(1);
  expect(f.previewServer.bodies[0]).toEqual({
    conversation_id: ID,
    action_type: 'create_case',
    payload: { ...toPayload(f.cases.getState().form) },
  });
  expect(f.cases.getState().draft?.destination.service_type).toBe('general');
  expect(f.cases.getState().draft?.summary).toBe(
    'Server preview: parking support request.',
  );
  expect(f.server.posts).toBe(0);
});
it('full replacement PATCH replaces routing/hash/summary and retains original deadline; DELETE is decoded as200', async () => {
  const f = make();
  await f.ready();
  await f.cases.create();
  const deadline = f.cases.getState().draft!.expires_at;
  f.cases.beginEdit();
  f.cases.input({
    ...f.cases.getState().form,
    summary: 'Updated report',
    transaction_reference: 'TX-9',
  });
  await f.cases.update();
  const body = f.previewServer.bodies[1] as {
    expected_version: number;
    payload: unknown;
  };
  expect(body.expected_version).toBe(1);
  expect(body.payload).toEqual(toPayload(f.cases.getState().form));
  expect(f.cases.getState().draft).toMatchObject({
    version: 2,
    payload_hash: 'b'.repeat(64),
    expires_at: deadline,
    destination: { queue_name: 'Updated general support' },
  });
  await f.cases.cancel();
  expect(f.previewServer.cancels).toBe(1);
  expect(f.cases.getState().draft?.state).toBe('cancelled');
});
it('409 fetches latest preview without repeating PATCH and preserves entered text for deliberate review', async () => {
  const f = make();
  await f.ready();
  await f.cases.create();
  f.cases.beginEdit();
  f.cases.input({ ...f.cases.getState().form, summary: 'My unsaved edit' });
  f.previewServer.draft.version = 8;
  await f.cases.update();
  expect(f.previewServer.patches).toBe(1);
  expect(f.cases.getState()).toMatchObject({
    notice: 'changed',
    draft: { version: 8 },
    form: { summary: 'My unsaved edit' },
  });
});
it('422 remains generic and preserves input; it is not fabricated field-level email validation', async () => {
  const f = make();
  await f.ready();
  await f.cases.create();
  f.cases.beginEdit();
  f.cases.input({ ...f.cases.getState().form, summary: 'Keep this edit' });
  f.previewServer.handler = (method) =>
    method === 'PATCH' ? Promise.resolve(error(422, 'VALIDATION_ERROR')) : null;
  await f.cases.update();
  expect(f.cases.getState()).toMatchObject({
    editing: true,
    form: { summary: 'Keep this edit' },
    uncertain: null,
    error: { status: 422 },
  });
});
it('unknown create stays latched across focus, routes and second create click without retrying a POST', async () => {
  const f = make();
  await f.ready();
  let posts = 0;
  f.previewServer.handler = (method) => {
    if (method === 'POST') {
      posts++;
      return Promise.reject(Error('Lost reply'));
    }
    return null;
  };
  await f.cases.create();
  expect(f.cases.getState().uncertain?.kind).toBe('create');
  await f.auth.recheck();
  await f.cases.enterIntake();
  await f.cases.create();
  expect(posts).toBe(1);
  expect(f.cases.getState().uncertain?.kind).toBe('create');
});
it('unknown PATCH with unchanged pending GET remains unknown and cannot be replayed', async () => {
  const f = make();
  await f.ready();
  await f.cases.create();
  f.cases.beginEdit();
  let patches = 0;
  f.previewServer.handler = (method) => {
    if (method === 'PATCH') {
      patches++;
      return Promise.reject(Error('Unknown effect'));
    }
    return null;
  };
  await f.cases.update();
  await f.cases.open(DRAFT);
  expect(f.cases.getState().uncertain?.kind).toBe('edit');
  await f.cases.update();
  expect(patches).toBe(1);
  f.previewServer.draft.version = 2;
  await f.cases.open(DRAFT);
  expect(f.cases.getState().uncertain).toBeNull();
  expect(f.cases.getState().notice).toBe('changed');
});
it('unknown DELETE cannot be considered failed from a pending snapshot; terminal cancellation resolves it', async () => {
  const f = make();
  await f.ready();
  await f.cases.create();
  let deletes = 0;
  f.previewServer.handler = (method) => {
    if (method === 'DELETE') {
      deletes++;
      return Promise.reject(Error('Unknown effect'));
    }
    return null;
  };
  await f.cases.cancel();
  await f.cases.open(DRAFT);
  await f.cases.cancel();
  expect(deletes).toBe(1);
  expect(f.cases.getState().uncertain?.kind).toBe('cancel');
  f.previewServer.draft.state = 'cancelled';
  await f.cases.open(DRAFT);
  expect(f.cases.getState().uncertain).toBeNull();
});
it('404 clears preview and form and never restores it from the old cache', async () => {
  const f = make();
  await f.ready();
  await f.cases.create();
  f.previewServer.handler = (method) =>
    method === 'GET' ? Promise.resolve(error(404, 'RESOURCE_NOT_FOUND')) : null;
  await f.cases.open(DRAFT);
  expect(f.cases.getState()).toMatchObject({
    phase: 'unavailable',
    draft: null,
    form: { contact_email: '', customer_report: '' },
  });
  expect(
    f.auth.retained('case-draft:/action-drafts/' + DRAFT, f.auth.capture()),
  ).toBeUndefined();
});
it('soft checking quarantines edits until fresh exact GET; account replacement drops private input', async () => {
  const f = make();
  await f.ready();
  await f.cases.create();
  f.cases.beginEdit();
  f.cases.input({ ...f.cases.getState().form, summary: 'Private edit' });
  await f.auth.recheck();
  expect(f.cases.getState().draft).toBeNull();
  await f.cases.open(DRAFT);
  expect(f.cases.getState().form.summary).toBe('Private edit');
  f.server.proof = 'z'.repeat(43);
  f.server.principal = ID;
  await f.auth.recheck();
  f.previewServer.handler = (method) =>
    method === 'GET' ? Promise.resolve(error(404, 'RESOURCE_NOT_FOUND')) : null;
  await f.cases.open(DRAFT);
  expect(f.cases.getState().form.summary).toBe('');
});
it('case intake retention does not overwrite the chat feature retained for the same authorized conversation', async () => {
  const f = make();
  await f.ready();
  await f.auth.recheck();
  await f.store.open(ID);
  expect(f.store.getState().input.body).toContain('flight changed');
  await f.cases.enterIntake();
  expect(f.cases.getState().form.contact_email).toBe('customer@example.com');
});
it('returning to the same intake after a known create opens the existing pointer rather than creating another', async () => {
  const f = make();
  await f.ready();
  await f.cases.create();
  await f.cases.prepareFromChat('contact', 'customer@example.com');
  expect(f.cases.getState().knownDraftId).toBe(DRAFT);
  expect(await f.cases.create()).toBeNull();
  expect(f.previewServer.creates).toBe(1);
});
it('late GET for another selection cannot replace the currently selected draft', async () => {
  const f = make();
  await f.ready();
  let finish!: (r: Response) => void;
  f.previewServer.handler = (method, path) =>
    method === 'GET' && path.endsWith(DRAFT)
      ? new Promise((r) => {
          finish = r;
        })
      : null;
  const old = f.cases.open(DRAFT);
  await Promise.resolve();
  await f.cases.open(NEXT);
  finish(json(f.previewServer.draft));
  await old;
  expect(f.cases.getState().draft?.draft_id).toBe(NEXT);
});
it('expired and committed responses are read-only; new review requires explicit renewal and never reuses old hash', async () => {
  const f = make();
  await f.ready();
  await f.cases.create();
  f.previewServer.draft.state = 'expired';
  await f.cases.open(DRAFT);
  await f.cases.update();
  expect(f.previewServer.patches).toBe(0);
  expect(await f.cases.renew()).toBe(true);
  expect(f.cases.getState()).toMatchObject({
    phase: 'intake',
    draft: null,
    knownDraftId: null,
  });
  expect(f.previewServer.creates).toBe(1);
  f.previewServer.draft.state = 'committed';
  f.previewServer.draft.result_ref = 'CASE-' + OTHER.replaceAll('-', '');
  await f.cases.open(DRAFT);
  await f.cases.cancel();
  expect(f.previewServer.cancels).toBe(0);
});
it('rejects malformed DTO/attachments and invalid input rather than carrying private fields or inventing defaults', () => {
  const f = make();
  const d = f.previewServer.draft;
  expect(() => decodeDraft({ ...d, version: 0 })).toThrow();
  expect(() =>
    decodeDraft({ ...d, payload: { ...d.payload, attachment_ids: [ID, ID] } }),
  ).toThrow();
  expect(() =>
    decodeDraft({ ...d, payload: { ...d.payload, payment_status: 'paid' } }),
  ).toThrow();
  expect(
    formSchema.safeParse({
      ...f.cases.getState().form,
      booking_reference: 'invalid / reference',
    }).success,
  ).toBe(false);
});
