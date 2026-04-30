import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './App.css';
import { checkBackendSessionAndMaybeReset } from './utils/sessionGuard';

// Wipe auto-persisted UI state if the backend has restarted since last visit.
// Blocks the React mount on the check so usePersistedState doesn't first
// hydrate with stale values (which would cause a brief flash of old data).
checkBackendSessionAndMaybeReset().finally(() => {
  ReactDOM.createRoot(document.getElementById('root')!).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>
  );
});
