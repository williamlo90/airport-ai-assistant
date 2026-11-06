import { createContext, useContext } from 'react';
import type { StaffStore } from './store';
export const StaffContext = createContext<StaffStore | null>(null);
export const useStaff = () => useContext(StaffContext);
