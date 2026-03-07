import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import { TickerCompare } from './components/debug/TickerCompare';
import './App.css';

const isDebug = window.location.pathname === '/debug';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    {isDebug ? <TickerCompare /> : <App />}
  </React.StrictMode>
);
