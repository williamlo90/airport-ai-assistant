import { afterEach, expect, it, vi } from 'vitest';
import { makeStaffFixture, caseId, denied } from '../../src/dev/staff-fixtures';
import { StaffQueueStore } from '../../src/features/staff/queue-store';
const owned: { dispose: () => void }[] = [];
async function make() {
  const f = makeStaffFixture();
  await f.ready();
  const store = new StaffQueueStore(f.staff, 'test');
  const off = store.connect();
  owned.push({
    dispose: () => {
      off();
      store.dispose();
      f.dispose();
    },
  });
  return { ...f, store };
}
afterEach(() => {
  owned.splice(0).forEach((v) => v.dispose());
  vi.restoreAllMocks();
});
it('sends explicit unassigned scoped pages, preserves cursor/filter and never infers totals', async () => {
  const f = await make();
  await f.store.ensureQueue();
  expect(f.store.getState().page?.view).toBe('unassigned');
  expect(f.store.getState().page?.items).toHaveLength(25);
  await f.store.next();
  expect(f.store.getState().pageNumber).toBe(2);
  expect(f.store.getState().page?.items).toHaveLength(20);
  expect(
    f.server.requests.filter((r) => r.path.includes('/queues/')).at(-1)?.path,
  ).toContain('cursor=p25');
  f.store.configure({ view: 'mine' });
  await f.store.ensureQueue();
  expect(f.store.getState().pageNumber).toBe(1);
  expect(f.store.getState().page?.items).toHaveLength(15);
  expect(f.server.requests.every((r) => r.method === 'GET')).toBe(true);
});
it('matches case and capabilities before exposing detail; version mismatch fails closed', async () => {
  const f = await make();
  await f.store.ensureQueue();
  await f.store.openCase(caseId(1));
  expect(f.store.getState().detail?.id).toBe(caseId(1));
  f.server.custom = (path) =>
    path.includes('/capabilities') ? Promise.resolve(f.json({})) : null;
  await f.store.openCase(caseId(2));
  expect(f.store.getState().detail).toBeNull();
  expect(f.store.getState().detailPhase).toBe('unavailable');
});
it('late queue response cannot replace a new filter, even when fetch ignores abort', async () => {
  const f = await make();
  let release!: (r: Response) => void;
  const held = new Promise<Response>((done) => {
    release = done;
  });
  f.server.custom = (path) =>
    path.includes('/queues/') && path.includes('view=unassigned') ? held : null;
  const old = f.store.ensureQueue();
  f.store.configure({ view: 'mine' });
  await f.store.ensureQueue();
  release(denied(503));
  await old;
  expect(f.store.getState().page?.view).toBe('mine');
  expect(f.store.getState().phase).toBe('ready');
});
it('revoked case removes cached row summary and detail; queue revoke removes the whole page', async () => {
  const f = await make();
  await f.store.ensureQueue();
  await f.store.openCase(caseId(1));
  f.server.custom = (path) =>
    path.includes('/staff/cases/') ? Promise.resolve(denied()) : null;
  await f.store.openCase(caseId(1), true);
  expect(f.store.getState().page?.items.some((v) => v.id === caseId(1))).toBe(
    false,
  );
  expect(f.store.getState().detail).toBeNull();
  f.server.custom = (path) =>
    path.includes('/queues/') ? Promise.resolve(denied()) : null;
  await f.store.loadQueue(true);
  expect(f.store.getState().page).toBeNull();
});
it('staff checking synchronously clears all rows, details, cursors and scroll', async () => {
  const f = await make();
  await f.store.ensureQueue();
  await f.store.next();
  await f.store.openCase(caseId(1));
  f.store.scrollTop = 150;
  const checking = f.auth.recheck();
  expect(f.store.getState().page).toBeNull();
  expect(f.store.getState().detail).toBeNull();
  expect(f.store.scrollTop).toBe(0);
  await checking;
});
