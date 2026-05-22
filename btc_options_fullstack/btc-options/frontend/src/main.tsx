import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './App.css';
import { checkBackendSessionAndMaybeReset } from './utils/sessionGuard';

// Tiny banner shown when the session-id check times out or errors. Lives in
// the global window so any component can dismiss it. Hard-coded styles to
// avoid pulling in CSS modules at top level.
function showBackendSlowBanner(reason: 'timeout' | 'error') {
  if (document.getElementById('be-slow-banner')) return;
  const div = document.createElement('div');
  div.id = 'be-slow-banner';
  div.style.cssText = [
    'position:fixed', 'top:0', 'left:0', 'right:0', 'z-index:99999',
    'background:#3a1f00', 'color:#fbbf24', 'padding:6px 14px',
    'fontFamily:system-ui,sans-serif', 'fontSize:12px',
    'borderBottom:1px solid #fbbf2455',
    'display:flex', 'alignItems:center', 'gap:12px',
  ].join(';');
  const msg = reason === 'timeout'
    ? '⚠ Backend slow/unreachable — UI loaded with stale state guard skipped.'
    : '⚠ Backend returned an error during session check — UI loaded without state wipe.';
  div.innerHTML = `<span>${msg}</span>` +
    `<button id="be-slow-retry" style="background:#1f6feb;color:#fff;border:none;padding:3px 10px;border-radius:3px;cursor:pointer;font-size:11px">Retry</button>` +
    `<button id="be-slow-dismiss" style="background:transparent;color:#fbbf24;border:1px solid #fbbf2455;padding:3px 8px;border-radius:3px;cursor:pointer;font-size:11px">Dismiss</button>`;
  document.body.appendChild(div);
  document.getElementById('be-slow-retry')?.addEventListener('click', () => {
    window.location.reload();
  });
  document.getElementById('be-slow-dismiss')?.addEventListener('click', () => {
    div.remove();
  });
}

// Wipe auto-persisted UI state if the backend has restarted since last visit.
// Awaits the check (with a 3s timeout inside sessionGuard) before mounting
// React. If the backend is hung, the check returns 'timeout' and we mount
// anyway — preventing the black-screen-on-backend-hang failure mode.
checkBackendSessionAndMaybeReset().then((result) => {
  if (result === 'timeout' || result === 'error') {
    showBackendSlowBanner(result);
  }
  ReactDOM.createRoot(document.getElementById('root')!).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>
  );
});
