import { createContext, useContext } from 'react';
import { QueryClient } from '@tanstack/react-query';
import type { AuthCoordinator } from '../../app/auth/coordinator';
import type { BoundarySnapshot, Decoder } from '../../api/contracts';
import { ApiFailure } from '../../api/errors';
import {
  lookupSchema,
  decodeBooking,
  decodeTransaction,
  decodePage,
} from './contracts';
import type {
  LookupInput,
  Booking,
  Transaction,
  TransactionPage,
} from './contracts';
interface State {
  input: LookupInput;
  formGeneration: number;
  phase: 'idle' | 'loading' | 'ready' | 'unavailable';
  booking: Booking | null;
  transaction: Transaction | null;
  page: TransactionPage | null;
  pageNumber: number;
  error: ApiFailure | null;
  retryAt: number;
}
const empty = (): State => ({
  input: { kind: 'booking', reference: '' },
  formGeneration: 0,
  phase: 'idle',
  booking: null,
  transaction: null,
  page: null,
  pageNumber: 0,
  error: null,
  retryAt: 0,
});
const failure = (e: unknown) =>
  e instanceof ApiFailure ? e : new ApiFailure('invalid-response', 'rejected');
export class SourceStore {
  private state = empty();
  private serial = 0;
  private busy = false;
  private retryAt = 0;
  private formGeneration = 0;
  private owner: BoundarySnapshot | null = null;
  private selected: LookupInput | null = null;
  private listeners = new Set<() => void>();
  readonly queries = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        staleTime: 0,
        gcTime: 0,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
      },
    },
  });
  constructor(
    readonly auth: AuthCoordinator,
    private scope: string,
  ) {}
  getState = () => this.state;
  subscribe = (f: () => void) => {
    this.listeners.add(f);
    return () => {
      this.listeners.delete(f);
    };
  };
  private set(value: Partial<State>) {
    this.state = {
      ...this.state,
      ...value,
      formGeneration: this.formGeneration,
      retryAt: this.retryAt,
    };
    this.listeners.forEach((f) => f());
  }
  connect() {
    const off = this.auth.onReset(() => this.detach()),
      source = this.auth.onSourceUnavailable((error) => {
        // Fence outstanding reads and purge private data before retaining only
        // the sanitized failure needed to explain why no record is shown.
        this.detach();
        this.fail(error);
      });
    return () => {
      off();
      source();
    };
  }
  changeInput(input: LookupInput) {
    if (
      input.kind === this.state.input.kind &&
      input.reference === this.state.input.reference
    )
      return;
    this.serial++;
    this.busy = false;
    this.owner = null;
    this.selected = null;
    this.queries.clear();
    this.set({ ...empty(), input });
  }
  private current(serial: number, owner: BoundarySnapshot) {
    return this.serial === serial && this.auth.isCurrent(owner);
  }
  private async get<T>(
    path: string,
    decode: Decoder<T>,
    serial: number,
    owner: BoundarySnapshot,
  ) {
    return this.queries.fetchQuery({
      queryKey: [
        this.scope,
        owner.boundaryId,
        owner.authEpoch,
        owner.sessionKind,
        owner.principalId,
        'source',
        path,
      ],
      queryFn: ({ signal }) =>
        this.auth.request({
          path,
          method: 'GET',
          boundary: owner,
          isRelevant: () => this.current(serial, owner),
          signal,
          successStatus: 200,
          decode,
        }),
    });
  }
  async lookup() {
    if (this.busy || Date.now() < this.retryAt) return;
    const input = lookupSchema.parse(this.state.input),
      owner = this.auth.capture();
    if (owner.sessionKind !== 'customer') return;
    const serial = ++this.serial;
    this.busy = true;
    this.owner = owner;
    this.selected = { ...input };
    this.queries.clear();
    this.set({ ...empty(), input, phase: 'loading' });
    try {
      if (input.kind === 'booking') {
        const booking = await this.get(
          '/bookings/' + input.reference,
          decodeBooking,
          serial,
          owner,
        );
        if (booking.reference !== input.reference)
          throw new ApiFailure('invalid-response', 'rejected');
        if (this.current(serial, owner)) this.set({ phase: 'ready', booking });
      } else {
        const transaction = await this.get(
          '/transactions/' + input.reference,
          decodeTransaction,
          serial,
          owner,
        );
        if (transaction.reference !== input.reference)
          throw new ApiFailure('invalid-response', 'rejected');
        if (this.current(serial, owner))
          this.set({ phase: 'ready', transaction });
      }
    } catch (e) {
      if (this.current(serial, owner)) this.fail(failure(e));
    } finally {
      if (this.serial === serial) this.busy = false;
    }
  }
  async transactions(next = false) {
    const selected = this.selected,
      old = this.state.page;
    if (
      this.busy ||
      Date.now() < this.retryAt ||
      !selected ||
      selected.kind !== 'booking' ||
      !this.state.booking ||
      !this.owner ||
      !this.auth.isCurrent(this.owner) ||
      (next && !old?.next_cursor)
    )
      return;
    const owner = this.auth.capture(),
      serial = ++this.serial,
      ref = selected.reference,
      limit = 20;
    const cursor = next ? old!.next_cursor : null,
      pageNumber = next ? this.state.pageNumber + 1 : 1;
    // Keep opaque bytes intact while using the transport's strict query alphabet.
    const query = new URLSearchParams({
      limit: String(limit),
      ...(cursor ? { cursor } : {}),
    })
      .toString()
      .replace(/\+/g, '%20')
      .replace(/\./g, '%2E')
      .replace(/\*/g, '%2A');
    this.busy = true;
    this.owner = owner;
    this.queries.clear();
    this.set({
      phase: 'loading',
      booking: null,
      transaction: null,
      page: null,
      pageNumber: 0,
      error: null,
    });
    try {
      const [booking, page] = await Promise.all([
        this.get('/bookings/' + ref, decodeBooking, serial, owner),
        this.get(
          '/bookings/' + ref + '/transactions?' + query,
          (v) => decodePage(v, ref, limit),
          serial,
          owner,
        ),
      ]);
      if (booking.reference !== ref)
        throw new ApiFailure('invalid-response', 'rejected');
      if (this.current(serial, owner))
        this.set({ phase: 'ready', booking, page, pageNumber });
    } catch (e) {
      if (this.current(serial, owner)) this.fail(failure(e));
    } finally {
      if (this.serial === serial) this.busy = false;
    }
  }
  private fail(error: ApiFailure) {
    this.queries.clear();
    this.selected = null;
    this.owner = null;
    if (error.status === 429 && error.retryAfterMs !== null)
      this.retryAt = Math.max(this.retryAt, Date.now() + error.retryAfterMs);
    const input =
      error.status === 403 || error.status === 404
        ? { kind: 'booking' as const, reference: '' }
        : this.state.input;
    if (error.status === 403 || error.status === 404) this.formGeneration++;
    this.set({ ...empty(), input, phase: 'unavailable', error });
  }
  detach() {
    this.serial++;
    this.busy = false;
    this.owner = null;
    this.selected = null;
    this.formGeneration++;
    this.queries.clear();
    this.set(empty());
  }
  dispose() {
    this.detach();
    this.listeners.clear();
  }
}
export const SourceContext = createContext<SourceStore | null>(null);
export const useSourceStore = () => useContext(SourceContext);
