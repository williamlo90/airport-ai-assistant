import { describe, expect, it } from 'vitest';
import {
  isResourceId,
  localizedTarget,
  readTopic,
  shouldBlockDirtyNavigation,
} from '../../src/app/routing/policy';
describe('route policy', () => {
  it('accepts opaque UUIDs, not arbitrary identifiers', () => {
    expect(isResourceId('11111111-1111-4111-8111-111111111111')).toBe(true);
    for (const id of [undefined, 'email@example.com', '123', '../private'])
      expect(isResourceId(id)).toBe(false);
  });
  it('rejects ambiguous topics and strips unapproved query and hash state', () => {
    expect(readTopic('?topic=booking&topic=payment')).toBeNull();
    expect(
      localizedTarget(
        '/help/new?topic=payment&email=a&otp=123&role=cs&tenant=x&payload=y#email=a',
        'id',
      ),
    ).toBe('/help/new?topic=payment&lang=id');
    expect(localizedTarget('/staff/cases?view=mine&status=closed', 'en')).toBe(
      '/staff/cases?status=closed&view=mine&lang=en',
    );
    for (const path of ['https://other.test', '//other.test', '/\\other.test'])
      expect(() => localizedTarget(path, 'en')).toThrow();
  });
  it('retains same issue for language and handoff, blocks a different flow', () => {
    const current = { pathname: '/help/new', search: '?topic=booking' };
    expect(
      shouldBlockDirtyNavigation(current, {
        ...current,
        search: '?topic=booking&lang=id',
      }),
    ).toBe(false);
    expect(
      shouldBlockDirtyNavigation(current, {
        ...current,
        search: '?topic=contact',
      }),
    ).toBe(false);
    expect(
      shouldBlockDirtyNavigation(current, {
        ...current,
        search: '?topic=payment',
      }),
    ).toBe(true);
    expect(
      shouldBlockDirtyNavigation(current, { pathname: '/', search: '' }),
    ).toBe(true);
  });
});
