import { expect, it } from 'vitest';
import {
  citationSource,
  decodeCitations,
} from '../../src/features/chat/citation-contracts';
import { ID, OTHER } from './fixture';

const valid = () => ({
  message_id: ID,
  status: 'historical_publication',
  issued_at: '2026-11-05T12:00:00Z',
  citations: [
    {
      version_id: ID,
      generation: OTHER,
      ordinal: 0,
      basis_hash: 'a'.repeat(64),
      document_id: OTHER,
      document_revision: 1,
      quote: '<b>Original source text</b>',
      source_uri: 'https://airport.example/policy',
    },
  ],
});
it('decodes a historical receipt and the legitimate empty/null response without inventing evidence', () => {
  expect(decodeCitations(valid(), ID).citations[0]?.quote).toBe(
    '<b>Original source text</b>',
  );
  expect(
    decodeCitations(
      {
        message_id: ID,
        status: 'historical_publication',
        issued_at: null,
        citations: [],
      },
      ID,
    ).citations,
  ).toEqual([]);
  expect(citationSource('urn:airport:parking/policy-1')).toBe('urn');
  expect(citationSource('https://airport.example/policy')).toBe('https');
});
it.each([
  'javascript:alert(1)',
  'http://airport.example',
  '//airport.example',
  'https://user:password@airport.example',
  'https://airport.example/?token=x',
  'https://airport.example/#fragment',
  'https://airport.example/?',
  'https://airport.example/#',
  'https://airport.example/\npolicy',
  'https://airport.example/\\policy',
  'urn:other:policy',
  'urn:airport:bad?query',
])('rejects unsafe or out-of-contract URI %s', (uri) => {
  expect(citationSource(uri)).toBeNull();
  const response = valid();
  response.citations[0]!.source_uri = uri;
  expect(() => decodeCitations(response, ID)).toThrow();
});
it('rejects mismatched receipts, unknown fields, impossible evidence and duplicate chunks', () => {
  expect(() => decodeCitations(valid(), OTHER)).toThrow();
  expect(() =>
    decodeCitations({ ...valid(), status: 'current' }, ID),
  ).toThrow();
  expect(() => decodeCitations({ ...valid(), issued_at: null }, ID)).toThrow();
  expect(() =>
    decodeCitations({ ...valid(), issued_at: 'yesterday' }, ID),
  ).toThrow();
  expect(() => decodeCitations({ ...valid(), tenant: ID }, ID)).toThrow();
  const response = valid();
  response.citations.push(response.citations[0]!);
  expect(() => decodeCitations(response, ID)).toThrow();
});
it.each([
  ['ordinal', -1],
  ['ordinal', 63],
  ['ordinal', 1.5],
  ['document_revision', 0],
  ['document_revision', Number.MAX_SAFE_INTEGER + 1],
  ['basis_hash', 'not-a-hash'],
  ['generation', 'wrong-id'],
  ['quote', ''],
  ['quote', '\0secret'],
  ['quote', 'x'.repeat(385)],
])('rejects invalid citation field %s', (field, value) => {
  const response = valid();
  Object.assign(response.citations[0]!, { [field]: value });
  expect(() => decodeCitations(response, ID)).toThrow();
});
