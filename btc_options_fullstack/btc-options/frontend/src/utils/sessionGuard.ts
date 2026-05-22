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

/**
 * Outcome of the session check, exposed to the React mount path so it can
 * surface "backend slow" UI when the check times out.
 *   - 'ok'      — backend responded, session check completed (matched or wiped)
 *   - 'timeout' — backend didn't respond within SESSION_CHECK_TIMEOUT_MS
 *   - 'error'   — backend responded with a non-2xx or fetch threw
 */
export type SessionCheckResult = 'ok' | 'timeout' | 'error';

const SESSION_CHECK_TIMEOUT_MS = 3000;

export async function checkBackendSessionAndMaybeReset(): Promise<SessionCheckResult> {
  const ac = new AbortController();
  const timer = window.setTimeout(() => ac.abort(), SESSION_CHECK_TIMEOUT_MS);
  try {
    const r = await fetch(`${API_BASE}/session-id`, { cache: 'no-store', signal: ac.signal });
    if (!r.ok) return 'error';
    const { session_id } = await r.json();
    const stored = localStorage.getItem(STORED_KEY);
    if (stored !== session_id) {
      wipeAutoState();
      localStorage.setItem(STORED_KEY, session_id);
    }
    return 'ok';
  } catch (e) {
    // AbortError fires when the timeout aborts the fetch.
    if ((e as Error)?.name === 'AbortError') return 'timeout';
    // Network failure, JSON parse error, etc. — leave state alone.
    return 'error';
  } finally {
    window.clearTimeout(timer);
  }
}
