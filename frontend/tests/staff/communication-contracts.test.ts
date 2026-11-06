import { describe, expect, it } from 'vitest';
import {
  decodeStaffChat,
  decodeChatAcceptance,
  decodeStaffReceipt,
  decodeStaffTranslation,
  decodeStaffCorrespondence,
} from '../../src/features/staff/communication-contracts';

const caseId = '11111111-1111-4111-8111-111111111111';
const conversationId = '22222222-2222-4222-8222-222222222222';
const member = '33333333-3333-4333-8333-333333333333';
const message = '44444444-4444-4444-8444-444444444444';
const chat = {
  conversation_id: conversationId,
  case_id: caseId,
  case_version: 2,
  version: 4,
  generation_epoch: 3,
  mode: 'human',
  handler_membership_id: member,
};

it('translation limits count Unicode codepoints consistently with backend and composer', () => {
  const text = '🙂'.repeat(4000);
  const value = {
    id: member,
    case_id: caseId,
    conversation_id: null,
    original_body: text,
    source_language: 'id',
    target_language: 'en',
    channel: 'email',
    state: 'ready',
    translated_body: text,
    reviewed_body: null,
    message_id: null,
    revision: 2,
    expires_at: '2026-11-06T12:00:00Z',
  };
  expect(decodeStaffTranslation(value, caseId, null).original_body).toBe(text);
  expect(() =>
    decodeStaffTranslation(
      { ...value, translated_body: text + '🙂' },
      caseId,
      null,
    ),
  ).toThrow();
});

describe('Staff communication boundaries', () => {
  it('requires the actual current conversation and human handler', () => {
    expect(decodeStaffChat(chat, caseId, conversationId).mode).toBe('human');
    expect(() => decodeStaffChat(chat, member, conversationId)).toThrow();
    expect(() =>
      decodeStaffChat(
        { ...chat, handler_membership_id: null },
        caseId,
        conversationId,
      ),
    ).toThrow();
    expect(() =>
      decodeChatAcceptance(
        { ...chat, state: 'accepted', mode: 'waiting_staff' },
        caseId,
        conversationId,
      ),
    ).toThrow();
    expect(
      decodeChatAcceptance(
        { ...chat, state: 'accepted' },
        caseId,
        conversationId,
      ).state,
    ).toBe('accepted');
  });
  it('allows case-only email receipts but never case-only live chat', () => {
    const receipt = {
      state: 'saved',
      message_id: message,
      conversation_id: null,
      case_id: caseId,
      sequence: 2,
      channel: 'email',
    };
    expect(decodeStaffReceipt(receipt, caseId, null, 'email').state).toBe(
      'saved',
    );
    expect(() =>
      decodeStaffReceipt({ ...receipt, channel: 'chat' }, caseId, null, 'chat'),
    ).toThrow();
    expect(() => decodeStaffReceipt(receipt, member, null, 'email')).toThrow();
    expect(() =>
      decodeStaffReceipt(receipt, caseId, conversationId, 'email'),
    ).toThrow();
    expect(() =>
      decodeStaffReceipt(
        { ...receipt, recipient: 'other@example.com' },
        caseId,
        null,
        'email',
      ),
    ).toThrow();
  });
  it('distinguishes generated drafts from an explicitly reviewed sent translation', () => {
    const draft = {
      id: member,
      case_id: caseId,
      conversation_id: conversationId,
      original_body: 'Mohon tunggu.',
      source_language: 'id',
      target_language: 'en',
      channel: 'email',
      state: 'ready',
      translated_body: 'Please wait.',
      reviewed_body: null,
      message_id: null,
      revision: 2,
      expires_at: '2026-11-06T12:00:00Z',
    };
    expect(decodeStaffTranslation(draft, caseId, conversationId).state).toBe(
      'ready',
    );
    expect(() =>
      decodeStaffTranslation(
        { ...draft, state: 'sent' },
        caseId,
        conversationId,
      ),
    ).toThrow();
    expect(() =>
      decodeStaffTranslation(
        { ...draft, target_language: 'id' },
        caseId,
        conversationId,
      ),
    ).toThrow();
    expect(() =>
      decodeStaffTranslation(draft, member, conversationId),
    ).toThrow();
    expect(
      decodeStaffTranslation(
        {
          ...draft,
          state: 'sent',
          reviewed_body: 'Please wait for our email.',
          message_id: message,
        },
        caseId,
        conversationId,
      ).reviewed_body,
    ).toBe('Please wait for our email.');
    expect(
      decodeStaffTranslation({ ...draft, conversation_id: null }, caseId, null)
        .channel,
    ).toBe('email');
    expect(() =>
      decodeStaffTranslation(
        { ...draft, conversation_id: null },
        caseId,
        conversationId,
      ),
    ).toThrow();
  });
  it('accepts only a scoped chronological page and its matching older position', () => {
    const item = {
      id: message,
      case_id: caseId,
      conversation_id: null,
      author_type: 'staff',
      body: 'Saved email reply',
      language: 'en',
      sequence: 2,
      channel: 'email',
      created_at: '2026-11-06T12:00:00Z',
    };
    const page = {
      items: [item],
      has_more: true,
      before_time: item.created_at,
      before_id: message,
    };
    expect(decodeStaffCorrespondence(page, caseId, 1).items).toHaveLength(1);
    expect(() => decodeStaffCorrespondence(page, member, 1)).toThrow();
    expect(() =>
      decodeStaffCorrespondence({ ...page, before_id: member }, caseId, 1),
    ).toThrow();
    expect(() =>
      decodeStaffCorrespondence({ ...page, items: [item, item] }, caseId, 2),
    ).toThrow();
    expect(() =>
      decodeStaffCorrespondence({ ...page, has_more: false }, caseId, 1),
    ).toThrow();
    expect(() =>
      decodeStaffCorrespondence(
        { ...page, items: [{ ...item, recipient: 'hidden@example.com' }] },
        caseId,
        1,
      ),
    ).toThrow();
    expect(
      decodeStaffCorrespondence(
        { items: [], has_more: false, before_time: null, before_id: null },
        caseId,
      ).items,
    ).toHaveLength(0);
  });
});
