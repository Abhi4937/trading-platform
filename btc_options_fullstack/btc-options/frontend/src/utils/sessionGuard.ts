/**
 * Backend-restart detector.
 *
 * On every page load we ask the backend for its `session_id` (a UUID generated
 * once at process start). If it differs from the value we last stored in
 * localStorage, the backend has restarted since our last visit — wipe the
 * auto-persisted UI state so the user sees a clean slate.
 *
 * What gets wiped: anything under the `historical:`, `backtest:`, or
 * `backtest_v1:` prefixes — i.e. the auto-saved date/time pickers, leg
 * configs, MTM data, last backtest result.
 *
 * What we KEEP:
 *   - `historical:savedStrategies` and `historical:strategy:<name>` —
 *     explicit user-named saves
 *   - the session-id record itself
 *
 * The check fires synchronously enough (single GET /api/v1/session-id) that
 * we'd rather block React mount on it: otherwise components hydrate with
 * stale data and we'd need a reload, which adds a visible flash.
 */

const STORED_KEY = 'app:backendSessionId';

const AUTO_PREFIXES = ['historical:', 'backtest:', 'backtest_v1:'];
// Anything matching this regex is an explicit user save — never wipe.
const KEEP_PATTERNS: RegExp[] = [
  /^historical:savedStrategies$/,
  /^historical:strategy:/,
  /^backtest:savedStrategies$/,
  /^backtest:strategy:/,
];

const API_BASE = (import.meta as any).env?.VITE_API_URL ?? 'http://localhost:8000/api/v1';

function shouldKeep(key: string): boolean {
  return KEEP_PATTERNS.some(re => re.test(key));
}

function wipeAutoState(): void {
  const keys: string[] = [];
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i);
    if (k) keys.push(k);
  }
  for (const k of keys) {
    if (k === STORED_KEY) continue;
    if (shouldKeep(k)) continue;
    if (AUTO_PREFIXES.some(p => k.startsWith(p))) {
      localStorage.removeItem(k);
    }
  }
}

export async function checkBackendSessionAndMaybeReset(): Promise<void> {
  try {
    const r = await fetch(`${API_BASE}/session-id`, { cache: 'no-store' });
    if (!r.ok) return;
    const { session_id } = await r.json();
    const stored = localStorage.getItem(STORED_KEY);
    if (stored !== session_id) {
      wipeAutoState();
      localStorage.setItem(STORED_KEY, session_id);
    }
  } catch {
    // Backend unreachable — leave state alone. The user will see the same
    // stale data, but blowing it away on a transient network blip would be
    // worse.
  }
}
