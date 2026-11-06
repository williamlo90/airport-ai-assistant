/// <reference types="node" />
import { webcrypto } from 'node:crypto';
import { afterEach, expect, it, vi } from 'vitest';
import { caseFixture, DRAFT, ID, OTHER, json, error } from './fixture';
import { attachmentId } from '../../src/features/cases/attachment-contracts';
import { decodeDraft } from '../../src/features/cases/contracts';
const owned: ReturnType<typeof caseFixture>[] = [];
afterEach(() => {
  owned.splice(0).forEach((f) => f.dispose());
  vi.unstubAllGlobals();
});
async function setup() {
  vi.stubGlobal('crypto', webcrypto);
  const f = caseFixture();
  owned.push(f);
  await f.ready();
  await f.cases.create();
  const posts: RequestInit[] = [],
    server = { lost: false, denied: false, mismatch: false, saved: true };
  f.server.override = async (path, init) => {
    if (!path.startsWith('/api/v1/attachments?'))
      return error(404, 'RESOURCE_NOT_FOUND');
    posts.push(init!);
    if (server.denied) return error(404, 'RESOURCE_NOT_FOUND');
    const key = new Headers(init?.headers).get('Idempotency-Key')!,
      id = await attachmentId(DRAFT, key);
    const file = init!.body as Blob;
    if (server.saved)
      f.previewServer.draft = {
        ...f.previewServer.draft,
        version: f.previewServer.draft.version + 1,
        payload_hash: 'c'.repeat(64),
        summary: 'File included in reviewed preview',
        payload: {
          ...f.previewServer.draft.payload,
          attachment_ids: [...f.previewServer.draft.payload.attachment_ids, id],
        },
      };
    if (server.lost) throw Error('lost response');
    return json(
      {
        attachment: {
          id: server.mismatch ? OTHER : id,
          byte_size: file.size,
          mime_type: file.type,
          scan_status: 'clean',
        },
        draft: f.previewServer.draft,
      },
      201,
    );
  };
  return { ...f, uploadPosts: posts, uploadServer: server };
}
const pdf = () =>
  new Blob(['%PDF-1.4\nsynthetic'], { type: 'application/pdf' });
it('UUIDv5 matches Python namespace/name behavior', async () => {
  vi.stubGlobal('crypto', webcrypto);
  expect(await attachmentId(DRAFT, ID)).toBe(
    '07ca31ab-9fad-5b74-a12b-1b6ae48a17d8',
  );
});
it('sends raw bytes, exact MIME/CSRF/key and updates selected files/hash/version without a filename or model call', async () => {
  const f = await setup(),
    file = pdf();
  await f.cases.upload(file);
  expect(f.uploadPosts).toHaveLength(1);
  expect(f.uploadPosts[0]?.body).toBe(file);
  const headers = new Headers(f.uploadPosts[0]?.headers);
  expect(headers.get('Content-Type')).toBe('application/pdf');
  expect(headers.get('X-CSRF-Token')).toBe('p'.repeat(43));
  expect(headers.get('Content-Disposition')).toBeNull();
  expect(f.cases.getState()).toMatchObject({
    phase: 'review',
    uncertain: null,
    draft: { version: 2, payload_hash: 'c'.repeat(64) },
  });
  expect(f.cases.getState().draft?.payload.attachment_ids).toHaveLength(1);
});
it('a lost upload is recovered with GET only, and another upload is blocked until selection is observed', async () => {
  const f = await setup();
  f.uploadServer.lost = true;
  await f.cases.upload(pdf());
  expect(f.cases.getState().uncertain?.kind).toBe('upload');
  await f.cases.upload(pdf());
  expect(f.uploadPosts).toHaveLength(1);
  await f.cases.open(DRAFT);
  expect(f.cases.getState().uncertain).toBeNull();
  expect(f.uploadPosts).toHaveLength(1);
  expect(f.cases.getState().draft?.payload.attachment_ids).toHaveLength(1);
});
it('a missing upload is never inferred successful from another draft version', async () => {
  const f = await setup();
  f.uploadServer.lost = true;
  f.uploadServer.saved = false;
  await f.cases.upload(pdf());
  f.previewServer.draft.version += 1;
  await f.cases.open(DRAFT);
  expect(f.cases.getState().uncertain?.kind).toBe('upload');
  expect(f.cases.getState().draft?.payload.attachment_ids).toEqual([]);
});
it('source purge erases private draft fields and retains only upload IDs for fresh authorized GET recovery', async () => {
  const f = await setup();
  f.uploadServer.lost = true;
  await f.cases.upload(pdf());
  await f.auth
    .request({
      path: '/source/check',
      method: 'GET',
      boundary: f.auth.capture(),
      isRelevant: () => true,
      successStatus: 200,
      decode: (v) => v,
    })
    .catch(() => {});
  // Trigger a real source-unavailable response on the controlled source route.
  f.server.override = async () => error(503, 'SOURCE_UNAVAILABLE');
  await f.auth
    .request({
      path: '/source/check',
      method: 'GET',
      boundary: f.auth.capture(),
      isRelevant: () => true,
      successStatus: 200,
      decode: (v) => v,
    })
    .catch(() => {});
  expect(f.cases.getState()).toMatchObject({
    phase: 'unavailable',
    draft: null,
  });
  f.server.override = null;
  await f.cases.open(DRAFT);
  expect(f.cases.getState().uncertain).toBeNull();
  expect(f.cases.getState().draft?.payload.attachment_ids).toHaveLength(1);
});
it('denied or malformed upload receipts cannot publish a selected file', async () => {
  const f = await setup();
  f.uploadServer.denied = true;
  await f.cases.upload(pdf());
  expect(f.cases.getState()).toMatchObject({
    phase: 'unavailable',
    draft: null,
  });
  f.uploadServer.denied = false;
  f.uploadServer.mismatch = true;
  await f.cases.open(DRAFT);
  await f.cases.upload(pdf());
  expect(f.cases.getState().uncertain?.kind).toBe('upload');
});
it('ordinary report edits retain attachment selection; removal uses exact current-version PATCH and rehashes', async () => {
  const f = await setup();
  await f.cases.upload(pdf());
  const ids = f.cases.getState().draft!.payload.attachment_ids;
  f.cases.beginEdit();
  f.cases.input({
    ...f.cases.getState().form,
    summary: 'Updated report with file',
  });
  await f.cases.update();
  expect(f.cases.getState().draft?.payload.attachment_ids).toEqual(ids);
  await f.cases.removeAttachment(ids[0]!);
  expect(f.cases.getState().draft?.payload.attachment_ids).toEqual([]);
  expect(f.previewServer.patches).toBe(2);
});
it('invalid types/size are rejected before dispatch and duplicate attachment IDs are invalid DTOs', async () => {
  const f = await setup();
  await f.cases.upload(new Blob(['bad'], { type: 'text/html' }));
  expect(f.uploadPosts).toHaveLength(0);
  const draft = f.previewServer.draft;
  expect(() =>
    decodeDraft({
      ...draft,
      payload: { ...draft.payload, attachment_ids: [ID, ID] },
    }),
  ).toThrow();
});
