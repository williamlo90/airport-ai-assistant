import {
  makeStaffFixture,
  MEMBER,
  caseId,
  denied,
} from '../../src/dev/staff-fixtures';
import { StaffQueueStore } from '../../src/features/staff/queue-store';
import { StaffCommunicationStore } from '../../src/features/staff/communication-store';
const messageId = 'a0000000-0000-4000-8000-000000000001';
const draftId = 'a0000000-0000-4000-8000-000000000002';
export async function communicationFixture() {
  const f = makeStaffFixture();
  await f.ready();
  const queue = new StaffQueueStore(f.staff, 'communication-test'),
    off = queue.connect();
  await queue.ensureQueue();
  await queue.openCase(caseId(4));
  const store = new StaffCommunicationStore(queue, caseId(4)),
    communicationOff = store.connect();
  const dispose = () => {
    communicationOff();
    off();
    queue.dispose();
    f.dispose();
  };
  const state = {
    lostSend: false,
    lostPrepare: false,
    lostApproval: false,
    receiptMissing: false,
    draft: null as Record<string, unknown> | null,
    sends: 0,
    prepares: 0,
    approvals: 0,
  };
  const receipt = {
    state: 'saved',
    message_id: messageId,
    conversation_id: null,
    case_id: caseId(4),
    sequence: 2,
    channel: 'email',
  };
  f.server.custom = (path, init) => {
    const url = new URL(path, 'https://test');
    if (url.pathname.endsWith('/communication-capabilities'))
      return Promise.resolve(
        f.json({
          case_id: caseId(4),
          case_version: 2,
          membership_id: MEMBER,
          email_available: true,
        }),
      );
    if (url.pathname.endsWith('/messages') && init?.method === 'GET')
      return Promise.resolve(
        f.json({
          items: [],
          has_more: false,
          before_time: null,
          before_id: null,
        }),
      );
    if (url.pathname.endsWith('/email-messages')) {
      state.sends++;
      return state.lostSend
        ? Promise.reject(new Error('Synthetic lost response'))
        : Promise.resolve(f.json(receipt, 201));
    }
    if (url.pathname.includes('/message-receipts/'))
      return Promise.resolve(
        state.receiptMissing ? denied(404) : f.json(receipt),
      );
    if (url.pathname.endsWith('/email-translation-drafts')) {
      state.prepares++;
      const body = JSON.parse(String(init?.body)) as {
        body: string;
        language: string;
        target_language: string;
      };
      state.draft = {
        id: draftId,
        case_id: caseId(4),
        conversation_id: null,
        original_body: body.body,
        source_language: body.language,
        target_language: body.target_language,
        channel: 'email',
        state: 'ready',
        translated_body: 'We will check.',
        reviewed_body: null,
        message_id: null,
        revision: 2,
        expires_at: '2027-01-01T12:00:00Z',
      };
      return state.lostPrepare
        ? Promise.reject(new Error('Synthetic lost response'))
        : Promise.resolve(f.json(state.draft, 201));
    }
    if (url.pathname.endsWith('/send')) {
      state.approvals++;
      state.draft = {
        ...state.draft,
        state: 'sent',
        reviewed_body: JSON.parse(String(init?.body)).text,
        message_id: messageId,
        revision: 3,
      };
      return state.lostApproval
        ? Promise.reject(new Error('Synthetic lost response'))
        : Promise.resolve(f.json(receipt, 201));
    }
    if (url.pathname.includes('/email-translation-'))
      return Promise.resolve(f.json(state.draft));
    return null;
  };
  return { ...f, queue, store, state, receipt, dispose };
}

export async function enableConversation(
  f: Awaited<ReturnType<typeof communicationFixture>>,
  lostAccept = false,
) {
  const cid = '70000000-0000-4000-8000-000000000007';
  const baseCap = f.queue.getState().capabilities!,
    baseDetail = f.queue.getState().detail!;
  const state = {
    accepts: 0,
    replies: 0,
    command: null as Record<string, unknown> | null,
    chat: {
      conversation_id: cid,
      case_id: caseId(4),
      case_version: 2,
      version: 2,
      generation_epoch: 1,
      mode: 'waiting_staff' as 'waiting_staff' | 'human',
      handler_membership_id: null as string | null,
    },
  };
  const original = f.server.custom!;
  f.server.custom = (path, init) => {
    const url = new URL(path, 'https://test');
    if (url.pathname === `/api/v1/staff/cases/${caseId(4)}`) {
      const last = baseDetail.history.at(-1)!;
      return Promise.resolve(
        f.json({
          ...baseDetail,
          version: state.chat.case_version,
          history:
            state.chat.case_version === 2
              ? baseDetail.history
              : [
                  ...baseDetail.history,
                  {
                    ...last,
                    event_type: 'status_changed',
                    previous_version: 2,
                    resulting_version: 3,
                    from_status: 'in_progress',
                    to_status: 'in_progress',
                    next_actor: 'staff',
                  },
                ],
        }),
      );
    }
    if (url.pathname.endsWith('/capabilities'))
      return Promise.resolve(
        f.json({
          ...baseCap,
          case_version: state.chat.case_version,
          active_conversation: {
            id: cid,
            version: state.chat.version,
            mode: state.chat.mode,
          },
        }),
      );
    if (url.pathname.endsWith('/communication-capabilities'))
      return Promise.resolve(
        f.json({
          case_id: caseId(4),
          case_version: state.chat.case_version,
          membership_id: MEMBER,
          email_available: true,
        }),
      );
    if (url.pathname.endsWith(`/conversations/${cid}`))
      return Promise.resolve(f.json(state.chat));
    if (url.pathname.endsWith(`/conversations/${cid}/accept`)) {
      state.accepts++;
      state.chat = {
        ...state.chat,
        case_version: 3,
        version: 3,
        generation_epoch: 2,
        mode: 'human',
        handler_membership_id: MEMBER,
      };
      return lostAccept
        ? Promise.reject(new Error('Synthetic lost accept response'))
        : Promise.resolve(f.json({ ...state.chat, state: 'accepted' }));
    }
    if (url.pathname.endsWith('/messages') && init?.method === 'POST') {
      state.replies++;
      state.command = JSON.parse(String(init.body));
      state.chat.version++;
      return Promise.resolve(
        f.json({ ...f.receipt, conversation_id: cid, channel: 'chat' }, 201),
      );
    }
    return original(path, init);
  };
  await f.queue.openCase(caseId(4), true);
  return state;
}
