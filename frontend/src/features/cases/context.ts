import { createContext, useContext } from 'react';
import type { CaseDraftStore } from './store';
export const CaseDraftContext = createContext<CaseDraftStore | null>(null);
export const useCaseDraft = () => useContext(CaseDraftContext);
