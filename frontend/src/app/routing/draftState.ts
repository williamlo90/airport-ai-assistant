import {
  createContext,
  useContext,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
} from 'react';
export class DraftRegistry {
  private entries = new Map<string, () => void>();
  private listeners = new Set<() => void>();
  snapshot = () => this.entries.size;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
  private emit() {
    this.listeners.forEach((listener) => listener());
  }
  register(id: string, discard: () => void) {
    this.entries.set(id, discard);
    this.emit();
    return () => {
      if (this.entries.delete(id)) this.emit();
    };
  }
  discardAll = () => {
    const callbacks = [...this.entries.values()];
    this.entries.clear();
    this.emit();
    callbacks.forEach((discard) => discard());
  };
}
export const DraftContext = createContext<DraftRegistry | null>(null);
export function useUnsavedChanges(dirty: boolean, onDiscard: () => void) {
  const registry = useContext(DraftContext);
  const id = useId();
  const latest = useRef(onDiscard);
  useLayoutEffect(() => {
    latest.current = onDiscard;
  }, [onDiscard]);
  useEffect(() => {
    if (!registry) throw new Error('Navigation draft provider required');
    if (dirty) return registry.register(id, () => latest.current());
  }, [registry, id, dirty]);
}
