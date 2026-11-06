import { afterEach, describe, expect, it, vi } from 'vitest';
import { createApiClient } from '../../src/api/client';
import type { BoundarySnapshot } from '../../src/api/contracts';
import {
  decodeSession,
  decodeChallenge,
  decodeEmpty,
  record,
} from '../../src/api/contracts';
import { ApiFailure, errorMessage, retryAfter } from '../../src/api/errors';
const id = '11111111-1111-4111-8111-111111111111';
const snapshot: BoundarySnapshot = Object.freeze({
  boundaryId: 'local-boundary',
  authEpoch: 1,
  sessionKind: 'customer',
  principalId: id,
  csrfProof: 'p'.repeat(43),
});
const valid = { session_kind: 'customer', principal_id: id };

it('raw attachment transport sends the exact Blob and MIME with existing CSRF/no-store protections', async () => {
  const f = fixture(),
    file = new Blob(['%PDF-1.4\nsynthetic'], { type: 'application/pdf' });
  await f.client.request({
    path: `/attachments?draft_id=${id}&expected_version=1`,
    method: 'POST',
    boundary: snapshot,
    isRelevant: () => true,
    successStatus: 200,
    decode: record,
    file,
    idempotencyKey: id,
  });
  const request = f.send.mock.calls[0]![1]!;
  expect(request.body).toBe(file);
  const headers = new Headers(request.headers);
  expect(headers.get('Content-Type')).toBe('application/pdf');
  expect(headers.get('X-CSRF-Token')).toBe(snapshot.csrfProof);
  expect(headers.get('Idempotency-Key')).toBe(id);
  expect(headers.get('Content-Disposition')).toBeNull();
  expect(request.cache).toBe('no-store');
  expect(request.redirect).toBe('error');
});
it('raw files cannot be mixed with JSON or sent to other endpoints/types', async () => {
  const f = fixture(),
    file = new Blob(['%PDF-1.4'], { type: 'application/pdf' });
  const request = {
    path: `/attachments?draft_id=${id}&expected_version=1`,
    method: 'POST' as const,
    boundary: snapshot,
    isRelevant: () => true,
    successStatus: 200,
    decode: record,
    file,
  };
  await expect(
    f.client.request({ ...request, body: {} }),
  ).rejects.toMatchObject({ outcome: 'not-sent' });
  await expect(
    f.client.request({ ...request, path: '/cases' }),
  ).rejects.toMatchObject({ outcome: 'not-sent' });
  await expect(
    f.client.request({
      ...request,
      file: new Blob(['x'], { type: 'text/html' }),
    }),
  ).rejects.toMatchObject({ outcome: 'not-sent' });
  expect(f.send).not.toHaveBeenCalled();
});
const json = (
  data: unknown,
  status = 200,
  headers: Record<string, string> = {},
) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  });
const errorBody = (code = 'VALIDATION_ERROR') => ({
  error: {
    code,
    message: 'SECRET_BACKEND_TEXT',
    request_id: id,
    retryable: true,
  },
});
function fixture(
  implementation: (
    ...args: Parameters<typeof fetch>
  ) => ReturnType<typeof fetch> = async () => json(valid),
) {
  let proof: string | null = snapshot.csrfProof;
  let current = true;
  const send = vi.fn(implementation),
    invalidate = vi.fn();
  const client = createApiClient({
    fetch: send,
    boundary: {
      isCurrent: (s) =>
        current &&
        s.boundaryId === snapshot.boundaryId &&
        s.authEpoch === snapshot.authEpoch,
      readCsrfProof: () => proof,
      invalidate,
    },
  });
  return {
    client,
    send,
    invalidate,
    changeProof: (v: string | null) => {
      proof = v;
    },
    expire: () => {
      current = false;
    },
  };
}
const request = {
  path: '/conversations',
  method: 'POST' as const,
  boundary: snapshot,
  isRelevant: () => true,
  decode: decodeSession,
  successStatus: 200,
  body: { message: 'PRIVATE_FORM_TEXT' },
};
afterEach(() => vi.useRealTimers());
describe('HTTP transport contract', () => {
  it('uses same-origin cookies, no-store, strict redirects and captured CSRF without Origin spoofing', async () => {
    const f = fixture();
    await f.client.request({ ...request, idempotencyKey: id });
    const [url, options] = f.send.mock.calls[0]!;
    expect(url).toBe('/api/v1/conversations');
    expect(options).toMatchObject({
      credentials: 'same-origin',
      mode: 'same-origin',
      cache: 'no-store',
      redirect: 'error',
      method: 'POST',
      body: JSON.stringify(request.body),
    });
    const headers = new Headers(options?.headers);
    expect(headers.get('X-CSRF-Token')).toBe('p'.repeat(43));
    expect(headers.get('Idempotency-Key')).toBe(id);
    expect(headers.has('Origin')).toBe(false);
    expect(headers.has('Authorization')).toBe(false);
  });
  it('rejects malformed or empty captured proof before headers/timers/dispatch', async () => {
    vi.useFakeTimers();
    for (const proof of ['', 'bad\r\nheader']) {
      const f = fixture();
      f.changeProof(proof);
      await expect(
        f.client.request({
          ...request,
          boundary: { ...snapshot, csrfProof: proof },
        }),
      ).rejects.toMatchObject({ kind: 'invalid-request', outcome: 'not-sent' });
      expect(f.send).not.toHaveBeenCalled();
      expect(vi.getTimerCount()).toBe(0);
    }
    expect(() =>
      decodeSession({ session_kind: ['customer'], principal_id: id }),
    ).toThrow();
  });
  it('GET omits body and CSRF and validates DTO', async () => {
    const f = fixture();
    await expect(
      f.client.request({ ...request, method: 'GET', body: undefined }),
    ).resolves.toEqual(valid);
    expect(f.send.mock.calls[0]![1]?.body).toBeUndefined();
    expect(
      new Headers(f.send.mock.calls[0]![1]?.headers).has('X-CSRF-Token'),
    ).toBe(false);
  });
  it('handles 204 without invoking JSON parsing', async () => {
    const response = new Response(null, { status: 204 });
    const parse = vi.spyOn(response, 'json');
    const f = fixture(async () => response);
    await expect(
      f.client.request({
        ...request,
        method: 'DELETE',
        successStatus: 204,
        decode: decodeEmpty,
      }),
    ).resolves.toBeUndefined();
    expect(parse).not.toHaveBeenCalled();
  });
  it.each([
    '//evil.test/x',
    'https://evil.test/x',
    '/../me',
    '/cases#payload',
    '/cases/%2e%2e/me',
    '/auth/sessions',
    '/me',
    '/staff/auth/login',
  ])(
    'rejects unsafe or coordinator-only path %s without dispatch',
    async (path) => {
      const f = fixture();
      await expect(
        f.client.request({ ...request, path }),
      ).rejects.toMatchObject({ kind: 'invalid-request', outcome: 'not-sent' });
      expect(f.send).not.toHaveBeenCalled();
    },
  );
  it('rejects changed proof or epoch before send instead of rebinding', async () => {
    const f = fixture();
    f.changeProof('new-account-proof');
    await expect(f.client.request(request)).rejects.toMatchObject({
      kind: 'stale',
      outcome: 'not-sent',
    });
    expect(f.send).not.toHaveBeenCalled();
    expect(f.invalidate).toHaveBeenCalledWith('boundary-changed');
    const g = fixture();
    g.expire();
    await expect(g.client.request(request)).rejects.toMatchObject({
      kind: 'stale',
    });
    expect(g.send).not.toHaveBeenCalled();
  });
  it('rejects late responses after identity/proof changes', async () => {
    let finish!: (r: Response) => void;
    const f = fixture(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const result = f.client.request(request);
    f.changeProof('rotated');
    finish(json(valid));
    await expect(result).rejects.toMatchObject({
      kind: 'stale',
      outcome: 'unknown',
    });
  });
  it('discards a response from an old resource/sequence without invalidating the session', async () => {
    let relevant = true;
    let finish!: (r: Response) => void;
    const f = fixture(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const pending = f.client.request({
      ...request,
      isRelevant: () => relevant,
    });
    relevant = false;
    finish(json(valid));
    await expect(pending).rejects.toMatchObject({ kind: 'stale' });
    expect(f.invalidate).not.toHaveBeenCalled();
  });
  it('checks boundary again after asynchronous body parsing', async () => {
    const response = json(valid);
    const f = fixture(async () => response);
    vi.spyOn(response, 'json').mockImplementation(async () => {
      f.expire();
      return valid;
    });
    await expect(f.client.request(request)).rejects.toMatchObject({
      kind: 'stale',
    });
  });
  it('invalidates 401 exactly once and never bootstraps or replays', async () => {
    const f = fixture(async () => json(errorBody('AUTH_REQUIRED'), 401));
    await expect(f.client.request(request)).rejects.toMatchObject({
      status: 401,
    });
    expect(f.invalidate).toHaveBeenCalledWith('unauthorized');
    expect(f.send).toHaveBeenCalledTimes(1);
  });
  it.each(['ACTION_FORBIDDEN', 'CSRF_REQUIRED'])(
    'classifies %s without clearing authorized input in the transport',
    async (code) => {
      const f = fixture(async () => json(errorBody(code), 403));
      await expect(f.client.request(request)).rejects.toMatchObject({
        kind: 'http',
        code,
        status: 403,
      });
      expect(f.invalidate).not.toHaveBeenCalled();
      expect(f.send).toHaveBeenCalledTimes(1);
    },
  );
  it('normalizes generic422 without invented field errors or raw messages', async () => {
    const f = fixture(async () => json(errorBody(), 422));
    try {
      await f.client.request(request);
      throw new Error('expected failure');
    } catch (e) {
      expect(e).toBeInstanceOf(ApiFailure);
      expect(e).toMatchObject({
        code: 'VALIDATION_ERROR',
        status: 422,
        requestId: id,
      });
      expect(e).not.toHaveProperty('fieldErrors');
      expect(JSON.stringify(e)).not.toContain('SECRET_BACKEND_TEXT');
      expect(errorMessage(e as ApiFailure, 'en')).toContain('format');
      expect(errorMessage(e as ApiFailure, 'id')).toContain('format');
    }
  });
  it('retains safe result_ref and Retry-After metadata but does not retry', async () => {
    const f = fixture(async () =>
      json(
        {
          ...errorBody('RATE_LIMITED'),
          error: {
            ...errorBody().error,
            code: 'RATE_LIMITED',
            result_ref: 'CASE-' + 'a'.repeat(32),
          },
        },
        429,
        { 'Retry-After': '20' },
      ),
    );
    await expect(f.client.request(request)).rejects.toMatchObject({
      retryAfterMs: 20000,
      resultRef: 'CASE-' + 'a'.repeat(32),
      serverRetryable: true,
    });
    expect(f.send).toHaveBeenCalledTimes(1);
  });
  it.each([
    json('bad'),
    json({ session_kind: 'customer', principal_id: null }),
    new Response('<html>SECRET</html>', {
      headers: { 'Content-Type': 'text/html' },
    }),
    new Response('{', { headers: { 'Content-Type': 'application/json' } }),
  ])('rejects malformed successes safely', async (response) => {
    const f = fixture(async () => response);
    await expect(f.client.request(request)).rejects.toMatchObject({
      kind: 'invalid-response',
      outcome: 'unknown',
    });
  });
  it('sanitizes malformed API error envelopes and respects HTTPstatus', async () => {
    const f = fixture(async () => json({ error: { message: 'PRIVATE' } }, 422));
    await expect(f.client.request(request)).rejects.toMatchObject({
      kind: 'http',
      code: 'HTTP_ERROR',
      status: 422,
      requestId: null,
    });
  });
  it('network failure is unknown for a dispatched mutation, with no retries', async () => {
    const f = fixture(async () => {
      throw new Error('PRIVATE_PROVIDER_URL');
    });
    await expect(f.client.request(request)).rejects.toMatchObject({
      kind: 'network',
      outcome: 'unknown',
    });
    expect(f.send).toHaveBeenCalledTimes(1);
  });
  it('abort propagates to fetch; pre-abort never dispatches', async () => {
    const stop = new AbortController();
    stop.abort();
    const f = fixture();
    await expect(
      f.client.request({ ...request, signal: stop.signal }),
    ).rejects.toMatchObject({ kind: 'aborted', outcome: 'not-sent' });
    expect(f.send).not.toHaveBeenCalled();
    const g = fixture(
      (_url, opts) =>
        new Promise((_resolve, reject) => {
          opts?.signal?.addEventListener('abort', () =>
            reject(new DOMException('aborted', 'AbortError')),
          );
        }),
    );
    const abort = new AbortController();
    const pending = g.client.request({ ...request, signal: abort.signal });
    abort.abort();
    await expect(pending).rejects.toMatchObject({
      kind: 'aborted',
      outcome: 'unknown',
    });
    expect(g.send.mock.calls[0]![1]?.signal?.aborted).toBe(true);
  });
  it('times out once and cleans up its timer', async () => {
    vi.useFakeTimers();
    const f = fixture(
      (_url, opts) =>
        new Promise((_resolve, reject) => {
          opts?.signal?.addEventListener('abort', () =>
            reject(new Error('aborted')),
          );
        }),
    );
    const pending = f.client.request({ ...request, timeoutMs: 25 });
    const assertion = expect(pending).rejects.toMatchObject({
      kind: 'timeout',
      outcome: 'unknown',
    });
    await vi.advanceTimersByTimeAsync(25);
    await assertion;
    expect(f.send).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });
  it('mutation5xx is uncertain even with a valid error response', async () => {
    const f = fixture(async () => json(errorBody('INTERNAL_ERROR'), 500));
    await expect(f.client.request(request)).rejects.toMatchObject({
      outcome: 'unknown',
      status: 500,
    });
  });
  it('bootstrap explicitly omits new CSRF and permits cookie rotation, while inspect rejects unstable proof', async () => {
    const f = fixture(async () => {
      f.changeProof('issued-proof');
      return json({ session_kind: 'anonymous', principal_id: null });
    });
    f.changeProof(null);
    await expect(
      f.client.session({
        operation: 'bootstrap',
        permit: { csrfProof: null, isCurrent: () => true },
        body: {},
        successStatus: 200,
        decode: decodeSession,
      }),
    ).resolves.toEqual({ session_kind: 'anonymous', principal_id: null });
    expect(
      new Headers(f.send.mock.calls[0]![1]?.headers).has('X-CSRF-Token'),
    ).toBe(false);
    const g = fixture(async () => {
      g.changeProof('other-proof');
      return json(valid);
    });
    await expect(
      g.client.session({
        operation: 'inspect',
        permit: { csrfProof: snapshot.csrfProof, isCurrent: () => true },
        successStatus: 200,
        decode: decodeSession,
      }),
    ).rejects.toMatchObject({ kind: 'stale' });
  });
  it('session mutations still require original proof and a current coordinator permit', async () => {
    const f = fixture();
    f.changeProof('different');
    await expect(
      f.client.session({
        operation: 'staff-login',
        permit: { csrfProof: snapshot.csrfProof, isCurrent: () => true },
        successStatus: 200,
        decode: decodeSession,
      }),
    ).rejects.toMatchObject({ kind: 'stale' });
    expect(f.send).not.toHaveBeenCalled();
    const g = fixture();
    g.changeProof(null);
    await expect(
      g.client.session({
        operation: 'logout',
        permit: { csrfProof: null, isCurrent: () => true },
        successStatus: 204,
        decode: decodeEmpty,
      }),
    ).rejects.toMatchObject({ kind: 'invalid-request' });
    expect(g.send).not.toHaveBeenCalled();
  });
});
describe('DTOs and error presentation', () => {
  it('requires session identity consistency and does not fabricate challenge expiry', () => {
    expect(() =>
      decodeSession({ session_kind: 'anonymous', principal_id: id }),
    ).toThrow();
    expect(() =>
      decodeSession({ session_kind: 'administrator', principal_id: id }),
    ).toThrow();
    expect(() => record([])).toThrow();
    expect(
      decodeChallenge({
        challenge_id: id,
        status: 'verification_requested',
        expiry: 'untrusted',
      }),
    ).toEqual({ challenge_id: id, status: 'verification_requested' });
    expect(() =>
      decodeChallenge({
        challenge_id: 'bad',
        status: 'verification_requested',
      }),
    ).toThrow();
    expect(() => decodeEmpty({})).toThrow();
  });
  it('supports seconds/HTTP-date Retry-After and ignores malformed values', () => {
    expect(
      retryAfter(
        'Wed, 04 Nov 2026 00:00:05 GMT',
        Date.parse('2026-11-04T00:00:00Z'),
      ),
    ).toBe(5000);
    expect(retryAfter('10', 0)).toBe(10000);
    expect(retryAfter('tomorrow', 0)).toBeNull();
    expect(retryAfter('-1', 0)).toBeNull();
  });
  it('provides safe bilingual messages for unknown codes and uncertain outcomes', () => {
    expect(
      errorMessage(new ApiFailure('http', 'rejected', 418, 'SECRET'), 'en'),
    ).not.toContain('SECRET');
    expect(errorMessage(new ApiFailure('timeout', 'unknown'), 'en')).toContain(
      'not confirmed',
    );
    expect(errorMessage(new ApiFailure('stale', 'not-sent'), 'id')).toContain(
      'tidak lagi berlaku',
    );
  });
});
