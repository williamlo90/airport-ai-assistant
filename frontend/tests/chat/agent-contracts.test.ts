import { expect, it } from 'vitest';
import {
  decodePrepared,
  decodeAgentOutcome,
  startAnswer,
} from '../../src/features/chat/agent-contracts';
import { ID, OTHER } from './fixture';

it('decodes only server-owned prepared and the four mutually exclusive outcomes', () => {
  expect(decodePrepared({ state: 'prepared', run_id: ID }).run_id).toBe(ID);
  for (const result of [
    { state: 'published', run_id: ID, message_id: OTHER, sequence: 2 },
    { state: 'discarded', run_id: ID, reason: 'STALE_GENERATION' },
    { state: 'review', run_id: ID, reason: 'PRIVATE_PERSISTENCE_REQUIRED' },
    { state: 'draft', run_id: ID, draft_id: OTHER },
  ])
    expect(decodeAgentOutcome(result, ID).state).toBe(result.state);
});
it('rejects wrong run, invented answer text, mixed outcome fields and malformed receipts', () => {
  for (const value of [
    { state: 'published', run_id: OTHER, message_id: ID, sequence: 1 },
    { state: 'published', run_id: ID, message_id: OTHER, sequence: 0 },
    {
      state: 'published',
      run_id: ID,
      message_id: OTHER,
      sequence: 1,
      body: 'fake answer',
    },
    { state: 'published', run_id: ID, draft_id: OTHER },
    { state: 'review', run_id: ID, draft_id: OTHER },
    { state: 'draft', run_id: ID },
    { state: 'running', run_id: ID },
  ])
    expect(() => decodeAgentOutcome(value, ID)).toThrow();
});
it('has only public category inputs; model, tenant and private references cannot be serialized', () => {
  const valid = {
    message_id: ID,
    request_key: OTHER,
    purpose: 'answer',
    service_type: 'parking',
  };
  expect(startAnswer.parse(valid)).toEqual(valid);
  for (const extra of [
    { tenant_id: ID },
    { booking_reference: 'SECRET' },
    { model: 'any' },
    { service_type: 'other' },
    { purpose: 'handoff' },
  ])
    expect(() => startAnswer.parse({ ...valid, ...extra })).toThrow();
});
it('private assistance accepts only one explicit booking/payment selector and never public-answer selectors', () => {
  const value = {
    message_id: ID,
    request_key: OTHER,
    purpose: 'assist',
    service_type: 'parking',
    booking_references: ['BOOK-1'],
    transaction_references: ['PAY-1'],
  };
  expect(startAnswer.parse(value)).toEqual(value);
  for (const change of [
    { purpose: 'answer' },
    { booking_references: ['A', 'B'] },
    { booking_references: ['bad space'] },
    { booking_references: [], transaction_references: [] },
    { principal_id: ID },
  ]) {
    expect(() => startAnswer.parse({ ...value, ...change })).toThrow();
  }
});
