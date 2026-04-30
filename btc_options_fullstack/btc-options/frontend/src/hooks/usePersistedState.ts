import { useState, useEffect, useRef } from 'react';

/**
 * useState that mirrors to localStorage so it survives mode switches and reloads.
 * Pass a unique `key` per piece of state.
 *
 * Caveat: only JSON-serialisable values. Don't pass functions or DOM nodes.
 */
export function usePersistedState<T>(
  key: string,
  initial: T,
): [T, React.Dispatch<React.SetStateAction<T>>] {
  const ranOnce = useRef(false);

  const [value, setValue] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key);
      if (raw == null) return initial;
      return JSON.parse(raw) as T;
    } catch {
      return initial;
    }
  });

  useEffect(() => {
    // Skip the very first render — value already came from localStorage
    if (!ranOnce.current) { ranOnce.current = true; return; }
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      // localStorage full or disabled — silently ignore
    }
  }, [key, value]);

  return [value, setValue];
}

/** Read a saved value once without subscribing. Returns null if missing/corrupt. */
export function readPersisted<T>(key: string): T | null {
  try {
    const raw = localStorage.getItem(key);
    return raw == null ? null : (JSON.parse(raw) as T);
  } catch {
    return null;
  }
}

/** Manually save (used by save-strategy buttons that take a snapshot). */
export function writePersisted(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* ignore */
  }
}

/** Delete a saved key. */
export function clearPersisted(key: string): void {
  try {
    localStorage.removeItem(key);
  } catch {
    /* ignore */
  }
}
