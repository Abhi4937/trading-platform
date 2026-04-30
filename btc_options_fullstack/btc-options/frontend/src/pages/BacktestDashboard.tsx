import React, { useEffect, useRef, useState } from 'react';
import { backtestApi } from '../services/backtest_api';
import type {
  BacktestRequest, BacktestResult, BacktestStatusResponse,
} from '../types/backtest';
import { BacktestForm } from '../components/backtest/BacktestForm';
import { BacktestProgressBar } from '../components/backtest/BacktestProgressBar';
import { BacktestEquityChart } from '../components/backtest/BacktestEquityChart';
import { BacktestDailyPnlBars } from '../components/backtest/BacktestDailyPnlBars';
import { BacktestStatsPanel } from '../components/backtest/BacktestStatsPanel';
import { BacktestTradeLogTable } from '../components/backtest/BacktestTradeLogTable';
import { readPersisted, writePersisted, clearPersisted } from '../hooks/usePersistedState';

const POLL_MS = 1000;
const RESULT_KEY = 'backtest:lastResult';

export const BacktestDashboard: React.FC = () => {
  const [jobId, setJobId] = useState<string | null>(null);
  // Hydrate from localStorage on mount so a previously-completed run survives
  // mode switches & reloads. We persist only on 'done' (see effect below) to
  // avoid thrashing localStorage with 1 Hz progress updates while running.
  const [status, setStatus] = useState<BacktestStatusResponse | null>(
    () => readPersisted<BacktestStatusResponse>(RESULT_KEY),
  );
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const timerRef = useRef<number | null>(null);

  useEffect(() => {
    if (status?.status === 'done') writePersisted(RESULT_KEY, status);
  }, [status]);

  // Polling loop driven by jobId.
  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const s = await backtestApi.poll(jobId);
        if (cancelled) return;
        setStatus(s);
        if (s.status === 'done' || s.status === 'error' || s.status === 'cancelled') {
          if (s.status === 'error') setError(s.error ?? 'Backtest failed');
          return;
        }
        timerRef.current = window.setTimeout(tick, POLL_MS);
      } catch (e: any) {
        if (cancelled) return;
        setError(`Polling failed: ${e.message ?? e}`);
      }
    };
    tick();
    return () => {
      cancelled = true;
      if (timerRef.current != null) {
        window.clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [jobId]);

  const handleSubmit = async (req: BacktestRequest) => {
    setError(null);
    setStatus(null);
    clearPersisted(RESULT_KEY);  // drop any previously-saved completed run
    setSubmitting(true);
    try {
      const r = await backtestApi.submit(req);
      setJobId(r.job_id);
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setSubmitting(false);
    }
  };

  const handleCancel = async () => {
    if (!jobId) return;
    try { await backtestApi.cancel(jobId); }
    catch (e: any) { setError(`Cancel failed: ${e.message ?? e}`); }
  };

  const result: BacktestResult | null = status?.result ?? null;
  const running = status && (status.status === 'queued' || status.status === 'running');
  const busy = submitting || !!running;

  return (
    <div style={{
      height: 'calc(100vh - 60px)',
      overflow: 'auto',
      background: '#080e16',
    }}>
      <BacktestForm busy={busy} onSubmit={handleSubmit} />

      {/* Status / results panel — slides in below the form */}
      {(status || error) && (
        <div style={{ padding: '0 20px 20px', background: '#080e16' }}>
          <div style={{
            display: 'flex', flexDirection: 'column', gap: 12,
            maxWidth: 1400, margin: '0 auto',
          }}>
            {error && (
              <div style={{
                background: '#3d0d12', border: '1px solid #ff4d6a',
                borderRadius: 6, padding: '10px 14px',
                color: '#ffb3c1', fontSize: 13,
              }}>
                {error}
              </div>
            )}

            {status && (
              <LightProgressBar
                status={status.status}
                daysDone={status.progress.days_done}
                daysTotal={status.progress.days_total}
                currentDate={status.progress.current_date}
                etaSeconds={status.progress.eta_seconds}
                onCancel={handleCancel}
              />
            )}

            {result && (
              <>
                <BacktestStatsPanel summary={result.summary} />

                <CardLight title="Equity Curve (Cumulative P&L)">
                  <BacktestEquityChart data={result.equity_curve} height={260} />
                </CardLight>

                <CardLight title="Daily P&L">
                  <BacktestDailyPnlBars data={result.equity_curve} height={200} />
                </CardLight>

                <BacktestTradeLogTable trades={result.trades} />
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

const CardLight: React.FC<{ title: string; children: React.ReactNode }> = ({ title, children }) => (
  <div style={{
    background: '#0d1421', border: '1px solid #1a2d42',
    borderRadius: 8, padding: 14,
  }}>
    <div style={{
      color: '#7a9bb5', fontSize: 11, fontWeight: 600,
      textTransform: 'uppercase', letterSpacing: 0.4, marginBottom: 8,
    }}>
      {title}
    </div>
    {children}
  </div>
);

interface LightProgressProps {
  status: string;
  daysDone: number;
  daysTotal: number;
  currentDate: string | null;
  etaSeconds: number | null;
  onCancel: () => void;
}

const LightProgressBar: React.FC<LightProgressProps> = ({
  status, daysDone, daysTotal, currentDate, etaSeconds, onCancel,
}) => {
  const total = Math.max(1, daysTotal);
  const pct = Math.min(100, (daysDone / total) * 100);
  const fmtEta = (s: number | null) => {
    if (s == null || s <= 0) return '—';
    if (s < 60) return `${Math.round(s)}s`;
    const m = Math.floor(s / 60);
    return `${m}m ${Math.round(s - m * 60)}s`;
  };
  const dotColor =
    status === 'done' ? '#10b981'
    : status === 'error' ? '#ef4444'
    : status === 'cancelled' ? '#f59e0b'
    : '#3b82f6';
  const label =
    status === 'done' ? 'Done'
    : status === 'error' ? 'Failed'
    : status === 'cancelled' ? 'Cancelled'
    : status === 'queued' ? 'Queued'
    : 'Running';
  return (
    <div style={{
      background: '#0d1421', border: '1px solid #1a2d42',
      borderRadius: 8, padding: '10px 14px',
      display: 'flex', alignItems: 'center', gap: 12,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 100 }}>
        <div style={{ width: 8, height: 8, borderRadius: '50%', background: dotColor }} />
        <span style={{ fontSize: 12, fontWeight: 600, color: '#c9d1d9' }}>{label}</span>
      </div>
      <div style={{ flex: 1, height: 18, background: '#131f2e', borderRadius: 4, position: 'relative' }}>
        <div style={{
          position: 'absolute', top: 0, left: 0, height: '100%',
          width: `${pct}%`, background: dotColor, borderRadius: 4,
          transition: 'width 200ms ease',
        }} />
        <div style={{
          position: 'absolute', inset: 0,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          color: '#fff', fontSize: 11, fontWeight: 600,
          textShadow: '0 1px 2px rgba(0,0,0,0.5)',
        }}>
          {daysDone} / {daysTotal} days{currentDate ? ` · ${currentDate}` : ''}
        </div>
      </div>
      <div style={{ color: '#7a9bb5', fontSize: 11, minWidth: 80, textAlign: 'right' }}>
        ETA {fmtEta(etaSeconds)}
      </div>
      {(status === 'queued' || status === 'running') && (
        <button onClick={onCancel} style={{
          background: 'transparent', border: '1px solid #ff4d6a',
          color: '#ff4d6a', borderRadius: 4,
          padding: '4px 12px', fontSize: 11, fontWeight: 600,
          cursor: 'pointer',
        }}>Cancel</button>
      )}
    </div>
  );
};
