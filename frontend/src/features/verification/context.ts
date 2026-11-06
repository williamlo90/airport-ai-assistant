import { createContext, useContext } from 'react';
import type { VerificationStore } from './store';
export const VerificationContext = createContext<VerificationStore | null>(
  null,
);
export const useVerification = () => useContext(VerificationContext);
