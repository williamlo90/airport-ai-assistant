import { record, uuid } from './contracts';
import type { Locale } from '../locales/messages';
export type FailureKind =
  | 'http'
  | 'network'
  | 'timeout'
  | 'aborted'
  | 'stale'
  | 'invalid-response'
  | 'invalid-request';
export class ApiFailure extends Error {
  constructor(
    readonly kind: FailureKind,
    readonly outcome: 'not-sent' | 'rejected' | 'unknown',
    readonly status: number | null = null,
    readonly code: string = 'REQUEST_FAILED',
    readonly requestId: string | null = null,
    readonly retryAfterMs: number | null = null,
    readonly resultRef: string | null = null,
    readonly serverRetryable = false,
  ) {
    super('API request did not complete');
    this.name = 'ApiFailure';
  }
}
export function retryAfter(value: string | null, now: number): number | null {
  if (value === null) return null;
  if (/^\d+$/.test(value)) {
    const ms = Number(value) * 1000;
    return Number.isSafeInteger(ms) ? ms : null;
  }
  // HTTP-date, not a permissive locale-dependent date string.
  if (
    !/^(Mon|Tue|Wed|Thu|Fri|Sat|Sun), \d{2} [A-Z][a-z]{2} \d{4} \d{2}:\d{2}:\d{2} GMT$/.test(
      value,
    )
  )
    return null;
  const at = Date.parse(value);
  return Number.isFinite(at) ? Math.max(0, at - now) : null;
}
export function httpFailure(
  status: number,
  value: unknown,
  retryHeader: string | null,
  now: number,
  mutation: boolean,
): ApiFailure {
  let code = 'HTTP_ERROR',
    requestId: string | null = null,
    resultRef: string | null = null,
    retryable = false;
  try {
    const e = record(record(value).error);
    if (
      typeof e.code !== 'string' ||
      !/^[A-Z][A-Z0-9_]{0,63}$/.test(e.code) ||
      typeof e.message !== 'string' ||
      !uuid(e.request_id) ||
      typeof e.retryable !== 'boolean'
    )
      throw new Error();
    code = e.code;
    requestId = e.request_id;
    retryable = e.retryable;
    if (
      typeof e.result_ref === 'string' &&
      /^CASE-[a-f0-9]{32}$/.test(e.result_ref)
    )
      resultRef = e.result_ref;
  } catch {
    /* Malformed errors never expose raw server data. */
  }
  return new ApiFailure(
    'http',
    mutation && (status >= 500 || status === 408) ? 'unknown' : 'rejected',
    status,
    code,
    requestId,
    status === 429 ? retryAfter(retryHeader, now) : null,
    resultRef,
    retryable,
  );
}
const copy = {
  en: {
    generic: 'The request could not be completed.',
    unknown:
      'The result is not confirmed. Check its status before trying again.',
    session: 'Your session needs to be checked before continuing.',
    forbidden:
      'This action is not allowed. Check your access before continuing.',
    csrf: 'Your session must be checked before sending this request again.',
    missing: 'This information is not available.',
    validation: 'Check the format and completeness of your request.',
    conflict: 'The information changed. Review it before continuing.',
    expired: 'This draft has expired. Start a new review.',
    limited: 'Too many requests. Wait before trying again.',
    unavailable: 'The service is temporarily unavailable.',
    cancelled: 'The request was cancelled.',
    stale: 'This response is no longer current and was discarded.',
  },
  id: {
    generic: 'Permintaan belum dapat diselesaikan.',
    unknown: 'Hasil belum dipastikan. Periksa statusnya sebelum mencoba lagi.',
    session: 'Sesi Anda perlu diperiksa sebelum melanjutkan.',
    forbidden:
      'Tindakan ini tidak diizinkan. Periksa akses Anda sebelum melanjutkan.',
    csrf: 'Sesi Anda harus diperiksa sebelum mengirim ulang permintaan ini.',
    missing: 'Informasi ini tidak tersedia.',
    validation: 'Periksa format dan kelengkapan permintaan Anda.',
    conflict: 'Informasi berubah. Tinjau kembali sebelum melanjutkan.',
    expired: 'Draf ini kedaluwarsa. Mulai tinjauan baru.',
    limited: 'Terlalu banyak permintaan. Tunggu sebelum mencoba lagi.',
    unavailable: 'Layanan sementara belum tersedia.',
    cancelled: 'Permintaan dibatalkan.',
    stale: 'Respons ini tidak lagi berlaku dan diabaikan.',
  },
} as const;
export function errorMessage(error: ApiFailure, locale: Locale): string {
  const c = copy[locale];
  if (error.kind === 'stale') return c.stale;
  if (error.status === 401) return c.session;
  if (error.code === 'CSRF_REQUIRED') return c.csrf;
  if (error.status === 403) return c.forbidden;
  if (error.outcome === 'unknown') return c.unknown;
  if (error.kind === 'aborted') return c.cancelled;
  if (error.status === 404) return c.missing;
  if (error.status === 422) return c.validation;
  if (error.status === 409) return c.conflict;
  if (error.status === 410) return c.expired;
  if (error.status === 429) return c.limited;
  if (error.status === 502 || error.status === 503) return c.unavailable;
  return c.generic;
}
