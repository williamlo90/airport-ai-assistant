import type { BoundaryPort, BoundarySnapshot, Decoder } from './contracts';
import { uuid } from './contracts';
import { ApiFailure, httpFailure } from './errors';

type Method = 'GET' | 'POST' | 'PATCH' | 'DELETE';
interface Common<T> {
  readonly decode: Decoder<T>;
  readonly successStatus: number;
  readonly body?: unknown;
  /** Raw single-file upload only; never multipart/filename or JSON serialization. */
  readonly file?: Blob;
  readonly signal?: AbortSignal;
  readonly timeoutMs?: number;
  readonly idempotencyKey?: string;
}
export interface BusinessRequest<T> extends Common<T> {
  readonly path: string;
  readonly method: Method;
  readonly boundary: BoundarySnapshot;
  /** Feature-owned resource/sequence check; stale queries must not overwrite a newer selection. */
  readonly isRelevant: () => boolean;
}
/** A coordinator must close the business gate BEFORE issuing a session permit. */
export interface SessionPermit {
  readonly csrfProof: string | null;
  isCurrent(): boolean;
}
type SessionOperation =
  | 'inspect'
  | 'inspect-staff'
  | 'bootstrap'
  | 'challenge'
  | 'verify'
  | 'staff-login'
  | 'logout';
export interface SessionRequest<T> extends Common<T> {
  readonly operation: SessionOperation;
  readonly permit: SessionPermit;
  readonly challengeId?: string;
}
const sessionRoutes = {
  inspect: ['GET', '/me'],
  'inspect-staff': ['GET', '/staff/me'],
  bootstrap: ['POST', '/auth/sessions'],
  challenge: ['POST', '/auth/challenges'],
  verify: ['POST', '/auth/challenges/'],
  'staff-login': ['POST', '/staff/auth/login'],
  logout: ['POST', '/auth/logout'],
} as const;
interface Options {
  readonly boundary: BoundaryPort;
  readonly fetch?: typeof fetch;
  readonly now?: () => number;
}
function safePath(path: string): string {
  // API-relative paths only. No redirects, fragments, encoded path separators or dot segments.
  if (
    !/^\/[a-zA-Z0-9/_-]+(?:\?[a-zA-Z0-9_=&%-]*)?$/.test(path) ||
    path.startsWith('//')
  )
    throw new ApiFailure('invalid-request', 'not-sent');
  const url = new URL('/api/v1' + path, 'https://api.invalid');
  if (
    !url.pathname.startsWith('/api/v1/') ||
    url.origin !== 'https://api.invalid'
  )
    throw new ApiFailure('invalid-request', 'not-sent');
  return url.pathname + url.search;
}
export function createApiClient(options: Options) {
  const send =
    options.fetch ?? ((...args: Parameters<typeof fetch>) => fetch(...args));
  const now = options.now ?? Date.now;
  async function execute<T>(
    request: Common<T>,
    method: Method,
    path: string,
    proof: string | null,
    current: () => boolean,
    business: boolean,
    allowRotation = false,
    isRelevant: () => boolean = () => true,
  ): Promise<T> {
    const mutation = method !== 'GET';
    let dispatched = false;
    let timedOut = false;
    let cancelled = false;
    const controller = new AbortController();
    const timeout = request.timeoutMs ?? 15000;
    if (!Number.isFinite(timeout) || timeout <= 0 || timeout > 120000)
      throw new ApiFailure('invalid-request', 'not-sent');
    const url = safePath(path);
    if (
      (business && proof === null) ||
      (proof !== null && !/^[A-Za-z0-9_-]{43}$/.test(proof))
    )
      throw new ApiFailure('invalid-request', 'not-sent');
    if (request.idempotencyKey !== undefined && !uuid(request.idempotencyKey))
      throw new ApiFailure('invalid-request', 'not-sent');
    let body: string | Blob | undefined;
    try {
      if (request.file !== undefined) {
        if (
          request.body !== undefined ||
          method !== 'POST' ||
          !url.startsWith('/api/v1/attachments?') ||
          !(request.file instanceof Blob) ||
          request.file.size < 1 ||
          request.file.size > 5242880 ||
          !['image/png', 'image/jpeg', 'application/pdf'].includes(
            request.file.type,
          )
        )
          throw new Error();
        body = request.file;
      }
      if (request.body !== undefined) {
        if (!mutation) throw new Error();
        body = JSON.stringify(request.body);
        if (body === undefined) throw new Error();
      }
    } catch {
      throw new ApiFailure('invalid-request', 'not-sent');
    }
    const valid = () =>
      current() &&
      ((dispatched && allowRotation) ||
        options.boundary.readCsrfProof() === proof);
    const ensureCurrent = () => {
      if (!isRelevant())
        throw new ApiFailure(
          'stale',
          dispatched && mutation ? 'unknown' : 'not-sent',
        );
      if (!valid()) {
        if (business) options.boundary.invalidate('boundary-changed');
        throw new ApiFailure(
          'stale',
          dispatched && mutation ? 'unknown' : 'not-sent',
        );
      }
    };
    // Snapshot proof is never replaced by the latest cookie, including manual retries.
    ensureCurrent();
    if (mutation && proof === null && path !== '/auth/sessions')
      throw new ApiFailure('invalid-request', 'not-sent');
    if (request.signal?.aborted) throw new ApiFailure('aborted', 'not-sent');
    const abort = () => {
      cancelled = true;
      controller.abort();
    };
    request.signal?.addEventListener('abort', abort, { once: true });
    const timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, timeout);
    const headers = new Headers({ Accept: 'application/json' });
    if (body !== undefined)
      headers.set(
        'Content-Type',
        typeof body === 'string' ? 'application/json' : body.type,
      );
    if (mutation && proof !== null) headers.set('X-CSRF-Token', proof);
    if (request.idempotencyKey)
      headers.set('Idempotency-Key', request.idempotencyKey);
    try {
      ensureCurrent();
      dispatched = true;
      const response = await send(url, {
        method,
        headers,
        ...(body === undefined ? {} : { body }),
        credentials: 'same-origin',
        mode: 'same-origin',
        cache: 'no-store',
        redirect: 'error',
        signal: controller.signal,
      });
      ensureCurrent();
      if (business && response.status === 401) {
        options.boundary.invalidate('unauthorized');
        throw new ApiFailure('http', 'rejected', 401, 'AUTH_REQUIRED');
      }
      let value: unknown;
      if (response.status !== 204) {
        if (
          !/^application\/json(?:\s*;|$)/i.test(
            response.headers.get('Content-Type') ?? '',
          )
        ) {
          if (!response.ok)
            throw httpFailure(
              response.status,
              null,
              response.headers.get('Retry-After'),
              now(),
              mutation,
            );
          throw new ApiFailure(
            'invalid-response',
            mutation ? 'unknown' : 'rejected',
            response.status,
          );
        }
        try {
          value = await response.json();
        } catch {
          ensureCurrent();
          if (cancelled || timedOut)
            throw new ApiFailure(
              timedOut ? 'timeout' : 'aborted',
              mutation ? 'unknown' : 'rejected',
            );
          throw response.ok
            ? new ApiFailure(
                'invalid-response',
                mutation ? 'unknown' : 'rejected',
                response.status,
              )
            : httpFailure(
                response.status,
                null,
                response.headers.get('Retry-After'),
                now(),
                mutation,
              );
        }
      }
      ensureCurrent();
      if (cancelled || timedOut)
        throw new ApiFailure(
          timedOut ? 'timeout' : 'aborted',
          mutation ? 'unknown' : 'rejected',
        );
      if (!response.ok)
        throw httpFailure(
          response.status,
          value,
          response.headers.get('Retry-After'),
          now(),
          mutation,
        );
      if (response.status !== request.successStatus)
        throw new ApiFailure(
          'invalid-response',
          mutation ? 'unknown' : 'rejected',
          response.status,
        );
      try {
        return request.decode(value);
      } catch {
        throw new ApiFailure(
          'invalid-response',
          mutation ? 'unknown' : 'rejected',
          response.status,
        );
      }
    } catch (error) {
      if (error instanceof ApiFailure) throw error;
      ensureCurrent();
      throw new ApiFailure(
        timedOut ? 'timeout' : cancelled ? 'aborted' : 'network',
        dispatched && mutation ? 'unknown' : 'rejected',
      );
    } finally {
      clearTimeout(timer);
      request.signal?.removeEventListener('abort', abort);
    }
  }
  return {
    request<T>(request: BusinessRequest<T>): Promise<T> {
      const snapshot = Object.freeze({ ...request.boundary });
      // Session endpoints must be issued explicitly by the coordinator, never as business calls.
      if (
        /^\/(?:auth(?:\/|\?|$)|staff\/(?:auth|me)(?:\/|\?|$)|me(?:\?|$))/.test(
          request.path,
        )
      )
        return Promise.reject(new ApiFailure('invalid-request', 'not-sent'));
      return execute(
        request,
        request.method,
        request.path,
        snapshot.csrfProof,
        () => options.boundary.isCurrent(snapshot),
        true,
        false,
        request.isRelevant,
      );
    },
    session<T>(request: SessionRequest<T>): Promise<T> {
      const [method, base] = sessionRoutes[request.operation];
      if (request.operation === 'verify' && !uuid(request.challengeId))
        return Promise.reject(new ApiFailure('invalid-request', 'not-sent'));
      const path =
        request.operation === 'verify'
          ? base + request.challengeId + '/verify'
          : base;
      return execute(
        request,
        method,
        path,
        request.permit.csrfProof,
        () => request.permit.isCurrent(),
        false,
        ['bootstrap', 'verify', 'staff-login', 'logout'].includes(
          request.operation,
        ),
      );
    },
  };
}
