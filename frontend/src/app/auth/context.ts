import { createContext, useContext, useSyncExternalStore } from 'react';
import type { AuthCoordinator, AuthState } from './coordinator';
export const AuthContext = createContext<AuthCoordinator | null>(null);
const unavailable: AuthState = Object.freeze({
  phase: 'unavailable',
  epoch: 0,
});
const noopSubscribe = () => () => {};
const noState = () => unavailable;
export function useAuthState(coordinator: AuthCoordinator | null) {
  return useSyncExternalStore(
    coordinator?.subscribe ?? noopSubscribe,
    coordinator?.getState ?? noState,
    noState,
  );
}
export function useAuth() {
  const coordinator = useContext(AuthContext);
  if (!coordinator) throw new Error('Live auth provider required');
  return coordinator;
}
