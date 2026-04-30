import React from 'react';
import type { BacktestProgress, BacktestStatus } from '../../types/backtest';

interface Props {
  status: BacktestStatus;
  progress: BacktestProgress;
  onCancel?: () => void;
}

function fmtEta(secs: number | null): string {
  if (secs == null || secs <= 0) return '—';
  if (secs < 60) return `${Math.round(secs)}s`;
  const m = Math.floor(secs / 60);
  const s = Math.round(secs - m * 60);
  return `${m}m ${s}s`;
}

export const BacktestProgressBar: React.FC<Props> = ({ status, progress, onCancel }) => {
  const total = Math.max(1, progress.days_total);
  const pct = Math.min(100, (progress.days_done / total) * 100);

  const dotColor =
    status === 'done'      ? '#00e5a0'
    : status === 'error'   ? '#ff4d6a'
    : status === 'cancelled' ? '#ffa940'
    : '#1f6feb';

  const label =
    status === 'done'      ? 'Done'
    : status === 'error'   ? 'Failed'
    : status === 'cancelled' ? 'Cancelled'
    : status === 'queued'  ? 'Queued'
    : 'Running';

  return (
    <div style={{
      background: '#0d1421', border: '1px solid #1a2d42',
      borderRadius: 6, padding: '10px 14px',
      display: 'flex', alignItems: 'center', gap: 14,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 110 }}>
        <div style={{ width: 8, height: 8, borderRadius: '50%', background: dotColor }} />
        <span style={{ color: '#c9d1d9', fontSize: 13, fontWeight: 600 }}>{label}</span>
      </div>

      <div style={{ flex: 1, position: 'relative', height: 18 }}>
        <div style={{
          position: 'absolute', inset: 0,
          background: '#131f2e', borderRadius: 4,
        }} />
        <div style={{
          position: 'absolute', top: 0, left: 0, height: '100%',
          width: `${pct}%`, borderRadius: 4,
          background: status === 'error'    ? '#ff4d6a'
                    : status === 'cancelled' ? '#ffa940'
                    : '#1f6feb',
          transition: 'width 200ms ease',
        }} />
        <div style={{
          position: 'absolute', inset: 0,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          color: '#e6edf3', fontSize: 11, fontWeight: 600,
          textShadow: '0 1px 2px rgba(0,0,0,0.7)',
        }}>
          {progress.days_done} / {progress.days_total} days
          {progress.current_date && ` · ${progress.current_date}`}
        </div>
      </div>

      <div style={{ color: '#7a9bb5', fontSize: 12, minWidth: 90, textAlign: 'right' }}>
        ETA {fmtEta(progress.eta_seconds)}
      </div>

      {(status === 'queued' || status === 'running') && onCancel && (
        <button
          onClick={onCancel}
          style={{
            background: 'transparent', border: '1px solid #ff4d6a',
            color: '#ff4d6a', borderRadius: 4, padding: '4px 12px',
            fontSize: 12, fontWeight: 600, cursor: 'pointer',
          }}
        >
          Cancel
        </button>
      )}
    </div>
  );
};
