import { describe, expect, it } from 'vitest';
import {
  createBootstrap,
  resolvePublicSite,
} from '../../src/app/config/publicSite';

describe('reviewed host configuration', () => {
  it.each(['localhost', 'LOCALHOST', 'localhost.', '127.0.0.1'])(
    'recognizes %s with backend-compatible canonicalization',
    (hostname) => {
      expect(resolvePublicSite(hostname)?.airportName).toBe(
        'Westbridge Airport',
      );
    },
  );

  it.each([
    '',
    'unknown.example',
    'localhost.evil.example',
    '127.0.0.1.evil.example',
    'constructor',
    '__proto__',
    'https://localhost',
    'localhost:5173',
    ' localhost',
    '::1',
    'x'.repeat(254),
  ])('fails closed for %s', (hostname) => {
    expect(resolvePublicSite(hostname)).toBeUndefined();
  });

  it('ignores host, tenant and brand claims in URL parameters', () => {
    expect(
      createBootstrap('unknown.example', '?host=localhost&tenant=airport-demo'),
    ).toEqual({ status: 'unconfigured' });
    const result = createBootstrap(
      'localhost',
      '?tenant=another-tenant&airportName=Other',
    );
    expect(result.status === 'configured' && result.site.airportName).toBe(
      'Westbridge Airport',
    );
  });

  it.each([
    '',
    '?lang=fr',
    '?lang=ID',
    '?lang=',
    '?lang=id&lang=en',
    '?tenant=id',
    '?lang=%3Cscript%3E',
  ])('defaults to English for missing or invalid language %s', (search) => {
    expect(createBootstrap('localhost', search)).toMatchObject({
      status: 'configured',
      locale: 'en',
    });
  });

  it('allows Indonesian without exposing an actor, tenant ID or credentials', () => {
    const state = createBootstrap('localhost', '?lang=id');
    expect(state).toMatchObject({ status: 'configured', locale: 'id' });
    if (state.status !== 'configured')
      throw new Error('Expected reviewed local configuration');
    expect(Object.keys(state.site).sort()).toEqual(
      [
        'airportName',
        'defaultLocale',
        'deployment',
        'productName',
        'supportedLocales',
      ].sort(),
    );
    expect(Object.isFrozen(state.site)).toBe(true);
  });
});
