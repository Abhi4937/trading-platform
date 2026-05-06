import React from 'react';
import type { BacktestSummary } from '../../types/backtest';

interface Props {
  summary: BacktestSummary;
}

interface Kpi {
  label: string;
  value: string;
  tone?: 'pos' | 'neg' | 'neutral';
}

function fmtUsd(n: number): string {
  const sign = n < 0 ? '-' : '';
  return `${sign}$${Math.abs(n).toFixed(2)}`;
}

export const BacktestStatsPanel: React.FC<Props> = ({ summary }) => {
  const kpis: Kpi[] = [
    { label: 'Net P&L',         value: fmtUsd(summary.net_pnl),
      tone: summary.net_pnl >= 0 ? 'pos' : 'neg' },
    { label: 'Gross P&L',       value: fmtUsd(summary.gross_pnl),
      tone: summary.gross_pnl >= 0 ? 'pos' : 'neg' },
    { label: 'Total Costs',     value: fmtUsd(summary.total_costs), tone: 'neutral' },
    { label: 'Win Rate',        value: `${summary.win_rate_pct.toFixed(1)}%`,
      tone: summary.win_rate_pct >= 50 ? 'pos' : 'neg' },
    { label: 'Trades',          value: `${summary.total_trades}` },
    { label: 'Wins / Losses',   value: `${summary.wins} / ${summary.losses}` },
    { label: 'Avg Win',         value: fmtUsd(summary.avg_win),  tone: 'pos' },
    { label: 'Total Win P&L',  value: fmtUsd(summary.total_wins_pnl),   tone: 'pos' },
    { label: 'Avg Loss',       value: fmtUsd(summary.avg_loss), tone: 'neg' },
    { label: 'Total Loss P&L', value: fmtUsd(summary.total_losses_pnl), tone: 'neg' },
    { label: 'Reward / Risk',
      value: summary.avg_loss < -0.01
        ? `${(summary.avg_win / Math.abs(summary.avg_loss) * 100).toFixed(0)}%`
        : '—',
      tone: summary.avg_win / Math.abs(summary.avg_loss || 1) >= 1 ? 'pos' : 'neg' },
    { label: 'Expectancy / trade', value: fmtUsd(summary.expectancy),
      tone: summary.expectancy >= 0 ? 'pos' : 'neg' },
    { label: 'Sharpe (daily, ann.)', value: summary.sharpe_daily.toFixed(2),
      tone: summary.sharpe_daily >= 0 ? 'pos' : 'neg' },
    { label: 'Max Drawdown',    value: fmtUsd(summary.max_drawdown), tone: 'neg' },
    { label: 'Avg Margin Used', value: fmtUsd(summary.avg_margin_used) },
    { label: 'Return on Margin', value: `${summary.return_on_margin_pct.toFixed(2)}%`,
      tone: summary.return_on_margin_pct >= 0 ? 'pos' : 'neg' },
    // Per-trade Max/Min MTM aggregates
    { label: 'Avg Max MTM',     value: fmtUsd(summary.avg_max_mtm),     tone: 'pos' },
    { label: 'Avg Max Net',     value: fmtUsd(summary.avg_max_pnl_net),
      tone: summary.avg_max_pnl_net >= 0 ? 'pos' : 'neg' },
    { label: 'Avg Min MTM',     value: fmtUsd(summary.avg_min_mtm),
      tone: summary.avg_min_mtm >= 0 ? 'pos' : 'neg' },
    { label: 'Avg Min Net',     value: fmtUsd(summary.avg_min_pnl_net),
      tone: summary.avg_min_pnl_net >= 0 ? 'pos' : 'neg' },
    { label: 'Best Max MTM',    value: fmtUsd(summary.best_max_mtm),    tone: 'pos' },
    { label: 'Best Max Net',    value: fmtUsd(summary.best_max_pnl_net),
      tone: summary.best_max_pnl_net >= 0 ? 'pos' : 'neg' },
    { label: 'Worst Min MTM',   value: fmtUsd(summary.worst_min_mtm),   tone: 'neg' },
    { label: 'Worst Min Net',   value: fmtUsd(summary.worst_min_pnl_net),
      tone: summary.worst_min_pnl_net >= 0 ? 'pos' : 'neg' },
  ];

  return (
    <div style={{
      background: '#0d1421', border: '1px solid #1a2d42',
      borderRadius: 6, padding: 14,
    }}>
      <div style={{
        display: 'grid', gap: 10,
        gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
      }}>
        {kpis.map(k => (
          <div key={k.label} style={{
            background: '#080e16', borderRadius: 4,
            padding: '8px 10px',
            borderLeft: `3px solid ${
              k.tone === 'pos' ? '#00e5a0'
              : k.tone === 'neg' ? '#ff4d6a'
              : '#4a6a85'
            }`,
          }}>
            <div style={{ color: '#7a9bb5', fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.4 }}>
              {k.label}
            </div>
            <div style={{
              color:
                k.tone === 'pos' ? '#00e5a0'
                : k.tone === 'neg' ? '#ff4d6a'
                : '#e6edf3',
              fontSize: 16, fontWeight: 700, marginTop: 2,
            }}>
              {k.value}
            </div>
          </div>
        ))}
      </div>

      {(summary.max_dd_peak_date || summary.max_dd_trough_date) && (
        <div style={{
          marginTop: 12, padding: '8px 10px',
          background: '#080e16', borderRadius: 4,
          color: '#7a9bb5', fontSize: 11,
        }}>
          Max DD spans <span style={{ color: '#c9d1d9' }}>{summary.max_dd_peak_date}</span>
          {' → '}
          <span style={{ color: '#c9d1d9' }}>{summary.max_dd_trough_date}</span>
          {' '}({summary.max_drawdown_pct.toFixed(2)}%)
        </div>
      )}
      {(summary.best_max_mtm_date || summary.worst_min_mtm_date) && (
        <div style={{
          marginTop: 8, padding: '8px 10px',
          background: '#080e16', borderRadius: 4,
          color: '#7a9bb5', fontSize: 11, lineHeight: 1.6,
        }}>
          Best Max MTM on <span style={{ color: '#c9d1d9' }}>{summary.best_max_mtm_date}</span>
          {summary.best_max_pnl_net_date !== summary.best_max_mtm_date && (
            <> · Best Max Net on <span style={{ color: '#c9d1d9' }}>{summary.best_max_pnl_net_date}</span></>
          )}
          <br/>
          Worst Min MTM on <span style={{ color: '#c9d1d9' }}>{summary.worst_min_mtm_date}</span>
          {summary.worst_min_pnl_net_date !== summary.worst_min_mtm_date && (
            <> · Worst Min Net on <span style={{ color: '#c9d1d9' }}>{summary.worst_min_pnl_net_date}</span></>
          )}
        </div>
      )}
    </div>
  );
};
