import { createContext, useContext } from 'react';
import type { AuthCoordinator } from '../../app/auth/coordinator';
import type { BoundarySnapshot } from '../../api/contracts';
import { uuid } from '../../api/contracts';
import { ApiFailure } from '../../api/errors';
import {
  informationMessage,
  decodeInformationPreview,
  decodeInformationReceipt,
} from './customer-contracts';
import type {
  InformationPreview,
  InformationReceipt,
} from './customer-contracts';
interface Command {
  caseId: string;
  previewId: string;
  key: string;
  version: number;
  boundaryId: string;
  principalId: string | null;
}
interface State {
  phase:
    | 'idle'
    | 'editing'
    | 'reviewing'
    | 'review'
    | 'sending'
    | 'unknown'
    | 'recovering'
    | 'saved';
  message: string;
  preview: InformationPreview | null;
  receipt: InformationReceipt | null;
  error: ApiFailure | null;
  retryAt: number;
}
const empty = (): State => ({
  phase: 'idle',
  message: '',
  preview: null,
  receipt: null,
  error: null,
  retryAt: 0,
});
const failure = (e: unknown) =>
  e instanceof ApiFailure ? e : new ApiFailure('invalid-response', 'unknown');
/** Review body/token live only in memory; uncertain writes retain IDs only and recover by GET. */
export class CustomerInformationStore {
  private state = empty();
  private serial = 0;
  private id: string | null = null;
  private version = 0;
  private closed = false;
  private commands = new Map<string, Command>();
  private listeners = new Set<() => void>();
  constructor(readonly auth: AuthCoordinator) {}
  get caseId() {
    return this.id;
  }
  getState = () => this.state;
  subscribe = (fn: () => void) => {
    this.listeners.add(fn);
    return () => {
      this.listeners.delete(fn);
    };
  };
  private set(value: Partial<State>) {
    this.state = { ...this.state, ...value };
    this.listeners.forEach((fn) => fn());
  }
  private customerOwner() {
    try {
      const owner = this.auth.capture();
      return owner.sessionKind === 'customer' ? owner : null;
    } catch {
      return null;
    }
  }
  connect() {
    const reset = this.auth.onReset(() => {
      this.serial++;
      if (this.auth.getState().phase !== 'checking') {
        this.id = null;
        this.commands.clear();
      }
      this.set({
        ...empty(),
        phase: this.id && this.commands.has(this.id) ? 'unknown' : 'idle',
      });
    });
    const checked = this.auth.subscribe(() => {
      const state = this.auth.getState();
      if (state.phase !== 'ready') return;
      const owner = this.customerOwner();
      if (!owner) return;
      for (const [id, command] of this.commands) {
        if (
          owner.sessionKind !== 'customer' ||
          owner.boundaryId !== command.boundaryId ||
          owner.principalId !== command.principalId
        )
          this.commands.delete(id);
      }
      if (
        this.state.phase === 'unknown' &&
        this.id &&
        !this.commands.has(this.id)
      )
        this.set(empty());
    });
    const source = this.auth.onSourceUnavailable((error) => {
      this.serial++;
      this.set({
        ...empty(),
        phase: this.id && this.commands.has(this.id) ? 'unknown' : 'idle',
        error,
      });
    });
    return () => {
      reset();
      checked();
      source();
      this.serial++;
      this.commands.clear();
      this.set(empty());
    };
  }
  open(id: string, version: number, closed: boolean) {
    if (!uuid(id)) return;
    if (id !== this.id) {
      this.serial++;
      this.id = id;
      this.set({
        ...empty(),
        phase: this.commands.has(id) ? 'unknown' : 'idle',
      });
    } else if (version !== this.version && !this.commands.has(id)) {
      this.serial++;
      this.set({
        ...empty(),
        phase: this.state.phase === 'saved' ? 'saved' : 'idle',
        receipt: this.state.receipt,
      });
    }
    this.version = version;
    this.closed = closed;
  }
  edit(message: string) {
    if (
      !this.id ||
      this.closed ||
      this.commands.has(this.id) ||
      ['reviewing', 'sending', 'recovering'].includes(this.state.phase)
    )
      return;
    this.set({
      phase: 'editing',
      message,
      preview: null,
      receipt: null,
      error: null,
    });
  }
  discard() {
    if (this.id && this.commands.has(this.id)) return; // Cannot forget an uncertain write to enable a new one.
    this.serial++;
    this.set(empty());
  }
  private current(serial: number, owner: BoundarySnapshot) {
    return serial === this.serial && this.auth.isCurrent(owner);
  }
  async review(language: 'en' | 'id') {
    if (
      !this.id ||
      this.closed ||
      this.commands.has(this.id) ||
      this.state.phase !== 'editing' ||
      Date.now() < this.state.retryAt
    )
      return;
    const owner = this.customerOwner();
    if (!owner) return;
    const parsed = informationMessage.safeParse(this.state.message);
    if (!parsed.success) {
      this.set({
        error: new ApiFailure('http', 'rejected', 422, 'VALIDATION_ERROR'),
      });
      return;
    }
    const message = parsed.data,
      id = this.id,
      version = this.version,
      serial = ++this.serial;
    this.set({ phase: 'reviewing', error: null, preview: null });
    try {
      const result = await this.auth.request({
        path: `/cases/${id}/information-previews`,
        method: 'POST',
        boundary: owner,
        isRelevant: () => this.current(serial, owner),
        successStatus: 200,
        body: { expected_version: version, message, language },
        decode: decodeInformationPreview,
      });
      if (!this.current(serial, owner)) return;
      if (
        result.case_id !== id ||
        result.expected_version !== version ||
        result.message !== message ||
        result.language !== language ||
        result.expires_at * 1000 <= Date.now()
      )
        throw new ApiFailure('invalid-response', 'rejected');
      this.set({ phase: 'review', preview: result });
    } catch (e) {
      if (!this.current(serial, owner)) return;
      const error = failure(e);
      this.set({
        phase:
          error.status === 403 || error.status === 404 ? 'idle' : 'editing',
        message: error.status === 403 || error.status === 404 ? '' : message,
        preview: null,
        error,
        retryAt:
          error.status === 429 ? Date.now() + (error.retryAfterMs ?? 1000) : 0,
      });
    }
  }
  private validate(receipt: InformationReceipt, command: Command) {
    if (
      receipt.case_id !== command.caseId ||
      receipt.preview_id !== command.previewId ||
      receipt.case_version !== command.version + 1
    )
      throw new ApiFailure('invalid-response', 'unknown');
    return receipt;
  }
  async confirm(confirmed: boolean) {
    const preview = this.state.preview,
      id = this.id;
    if (
      confirmed !== true ||
      !id ||
      !preview ||
      this.closed ||
      this.state.phase !== 'review' ||
      this.commands.has(id) ||
      Date.now() < this.state.retryAt
    )
      return;
    if (
      preview.expires_at * 1000 <= Date.now() ||
      preview.expected_version !== this.version
    ) {
      this.set({
        phase: 'editing',
        preview: null,
        error: new ApiFailure('http', 'rejected', 410, 'PREVIEW_EXPIRED'),
      });
      return;
    }
    const owner = this.customerOwner();
    if (!owner) return;
    const command: Command = {
      caseId: id,
      previewId: preview.preview_id,
      key: crypto.randomUUID(),
      version: preview.expected_version,
      boundaryId: owner.boundaryId,
      principalId: owner.principalId,
    };
    const serial = ++this.serial;
    this.commands.set(id, command);
    this.set({ phase: 'sending', message: '', preview: null, error: null });
    try {
      const result = await this.auth.request({
        path: `/cases/${id}/information`,
        method: 'POST',
        boundary: owner,
        isRelevant: () => this.current(serial, owner),
        idempotencyKey: command.key,
        successStatus: 201,
        body: {
          confirmation_token: preview.confirmation_token,
          confirmed: true,
        },
        decode: decodeInformationReceipt,
      });
      if (!this.current(serial, owner)) return;
      const receipt = this.validate(result, command);
      this.commands.delete(id);
      this.set({ phase: 'saved', receipt });
    } catch (e) {
      if (!this.current(serial, owner)) return;
      const error = failure(e);
      if (error.outcome !== 'unknown') this.commands.delete(id);
      this.set({
        ...empty(),
        phase: error.outcome === 'unknown' ? 'unknown' : 'idle',
        error,
        retryAt:
          error.status === 429 ? Date.now() + (error.retryAfterMs ?? 1000) : 0,
      });
    }
  }
  async recover() {
    const id = this.id,
      command = id ? this.commands.get(id) : undefined;
    if (
      !id ||
      !command ||
      this.state.phase !== 'unknown' ||
      Date.now() < this.state.retryAt ||
      this.auth.getState().phase !== 'ready'
    )
      return;
    const owner = this.customerOwner();
    if (
      !owner ||
      owner.boundaryId !== command.boundaryId ||
      owner.principalId !== command.principalId
    )
      return;
    const serial = ++this.serial;
    this.set({ ...empty(), phase: 'recovering' });
    try {
      const value = await this.auth.readResource({
        path: `/cases/${id}/information-receipts/${command.key}`,
        method: 'GET',
        boundary: owner,
        isRelevant: () => this.current(serial, owner),
        successStatus: 200,
        decode: decodeInformationReceipt,
      });
      if (!this.current(serial, owner)) return;
      const receipt = this.validate(value, command);
      this.commands.delete(id);
      this.set({ phase: 'saved', receipt });
    } catch (e) {
      if (!this.current(serial, owner)) return;
      const error = failure(e);
      this.set({
        ...empty(),
        phase: 'unknown',
        error,
        retryAt:
          error.status === 429 ? Date.now() + (error.retryAfterMs ?? 1000) : 0,
      });
    }
  }
}
export const CustomerInformationContext =
  createContext<CustomerInformationStore | null>(null);
export const useCustomerInformation = () =>
  useContext(CustomerInformationContext);
