import { makeStaffFixture, caseId } from '../../src/dev/staff-fixtures';
import { StaffQueueStore } from '../../src/features/staff/queue-store';
import { StaffMailStore } from '../../src/features/staff/mail-store';
export { caseId };
export const emailId = (n: number) =>
  'a0000000-0000-4000-8000-' + n.toString().padStart(12, '0');
export const delivery = (n = 1) => ({
  job_id: emailId(n),
  app_message_id: null,
  email_message_id: null,
  delivery_status: 'queued' as string,
  delivery_version: null,
  needs_review: false,
  review_reason: null as string | null,
  queued_at: '2026-11-05T12:00:00Z',
  provider_status_at: null,
  last_checked_at: null,
});
export const reply = (n = 1) => ({
  email_id: emailId(n),
  state: 'published' as string,
  reason: null as string | null,
  app_message_id: emailId(n + 100) as string | null,
  created_at: '2026-11-05T12:00:00Z',
  body: '<b>Original customer email</b>' as string | null,
  verification_state: null as string | null,
  send_state: null as string | null,
  attempts: null as number | null,
});
export async function mailFixture() {
  const f = makeStaffFixture();
  await f.ready();
  const queue = new StaffQueueStore(f.staff, 'mail-test'),
    queueOff = queue.connect();
  await queue.ensureQueue();
  await queue.openCase(caseId(1));
  const feed = {
    deliveries: { items: [delivery()], has_more: false },
    replies: [reply()],
  };
  f.server.custom = (path) =>
    path.endsWith('/email-deliveries')
      ? Promise.resolve(f.json(feed.deliveries))
      : path.endsWith('/email-replies')
        ? Promise.resolve(f.json(feed.replies))
        : null;
  const mail = new StaffMailStore(queue, caseId(1)),
    mailOff = mail.connect();
  return {
    ...f,
    feed,
    queue,
    mail,
    dispose() {
      mailOff();
      queueOff();
      queue.dispose();
      f.dispose();
    },
  };
}
