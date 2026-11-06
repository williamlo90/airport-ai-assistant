import { AuthCoordinator } from './coordinator';
import { readCsrfCookie } from './contracts';
import type { SignalPort } from './contracts';
/** No token, role, actor or tenant is read from URL/storage. */
export function createBrowserAuth() {
  let channel: BroadcastChannel | null = null;
  try {
    if (typeof BroadcastChannel !== 'undefined')
      channel = new BroadcastChannel('airport-auth-v1');
  } catch {
    /* send-time proof binding remains required */
  }
  const signals: SignalPort = {
    publish: (signal) => channel?.postMessage(signal),
    subscribe: (receive) => {
      const handler = (event: MessageEvent) => receive(event.data);
      channel?.addEventListener('message', handler);
      return () => channel?.removeEventListener('message', handler);
    },
  };
  const enabled = window.isSecureContext && location.protocol === 'https:';
  const coordinator = new AuthCoordinator({
    enabled,
    signals,
    readProof: () => {
      try {
        return readCsrfCookie(document.cookie);
      } catch {
        return null;
      }
    },
  });
  const refresh = () => {
    if (document.visibilityState === 'visible') void coordinator.recheck();
  };
  window.addEventListener('focus', refresh);
  window.addEventListener('online', refresh);
  document.addEventListener('visibilitychange', refresh);
  let disposed = false;
  return {
    coordinator,
    start: () => coordinator.start(),
    dispose: () => {
      if (disposed) return;
      disposed = true;
      window.removeEventListener('focus', refresh);
      window.removeEventListener('online', refresh);
      document.removeEventListener('visibilitychange', refresh);
      coordinator.dispose();
      channel?.close();
      channel = null;
    },
  };
}
