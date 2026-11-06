import { createContext, useContext } from 'react';
import type { ChatStore } from './store';
export const ChatContext = createContext<ChatStore | null>(null);
export const useChatStore = () => useContext(ChatContext);
