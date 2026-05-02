import React, { useEffect, useMemo, useRef, useState } from 'react';
import { backtestApi } from '../services/backtest_api';
import { historicalApi } from '../services/historical_api';
import type {
  BacktestRequest, BacktestResult, BacktestStatusResponse,
} from '../types/backtest';
import type { SpotOhlcBar, IndicatorConfig, IndicatorPoint } from '../types/historical';
import { BacktestForm } from '../components/backtest/BacktestForm';
import { BacktestProgressBar } from '../components/backtest/BacktestProgressBar';
import { BacktestEquityChart } from '../components/backtest/BacktestEquityChart';
import { BacktestDailyPnlBars } from '../components/backtest/BacktestDailyPnlBars';
import { BacktestStatsPanel } from '../components/backtest/BacktestStatsPanel';
import { BacktestTradeLogTable } from '../components/backtest/BacktestTradeLogTable';
import { SpotChart } from '../components/historical/SpotChart';
import { IndicatorConfigPanel } from '../components/historical/IndicatorConfigPanel';
import { StrangleAnalyticsPanel } from '../components/strangle/StrangleAnalyticsPanel';
import type {
  AnalyticsContext, AnalyticsLeg, CalibrationBucket,
} from '../utils/strangleAnalytics';
import type { BacktestTrade } from '../types/backtest';
import { readPersisted, writePersisted, clearPersisted, usePersistedState } from '../hooks/usePersistedState';

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

  // ── Spot/leg chart with technical indicators (shares config with Historical) ──
  const [indicatorConfigs, setIndicatorConfigs] = usePersistedState<IndicatorConfig[]>('historical:indicators', []);
  const [chartSource, setChartSource] = usePersistedState<'spot' | 'leg'>('backtest:chartSource', 'spot');
  const [chartTimeframe] = usePersistedState<string>('historical:chartTimeframe', '5m');

  // ── Strangle analytics for selected trade ──
  const [selectedTrade, setSelectedTrade] = useState<BacktestTrade | null>(null);
  const [selectedTradeAnalytics, setSelectedTradeAnalytics] = useState<{
    legs: AnalyticsLeg[]; ctx: AnalyticsContext | null;
    calibration: CalibrationBucket | null; loading: boolean;
  } | null>(null);

  useEffect(() => {
    if (!selectedTrade) {
      setSelectedTradeAnalytics(null);
      return;
    }
    // Build the AnalyticsContext directly from baked-in trade fields (no fetch needed).
    const t = selectedTrade;
    const tsParts = (t.entry_time ?? '').split(' ');
    // entry_time is "HH:MM:SS IST" — derive unix from date + time
    const entryUnix = (() => {
      try {
        const [h, m, s] = (tsParts[0] ?? '00:00:00').split(':').map(Number);
        const utc = Date.parse(`${t.date}T${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s||0).padStart(2,'0')}Z`);
        return Math.floor(utc / 1000) - 5.5 * 3600;   // IST → UTC
      } catch { return 0; }
    })();

    const expiry = t.legs[0]?.expiry ?? t.date;
    const expiryUnix = Math.floor(Date.parse(`${expiry}T12:00:00Z`) / 1000);
    const dte = Math.max(0, (expiryUnix - entryUnix) / 86400);

    // Build legs from trade fills (entry-side marks/Greeks already on each LegFill)
    const aLegs: AnalyticsLeg[] = t.legs.map(l => ({
      type: l.type,
      side: l.action,
      strike: l.strike,
      qty: l.qty,
      mark: l.entry_mark,
      iv: ((l as any).entry_iv ?? 0) / 100,
      delta: (l as any).entry_delta ?? 0,
      gamma: 0, vega: 0, theta: 0,
    }));

    const ctx: AnalyticsContext = {
      ts_unix: entryUnix,
      spot: t.spot_at_entry ?? 0,
      dte,
      expiry_ts: expiryUnix,
      atm_iv_7d: t.atm_iv_7d_at_entry ?? (t.entry_atm_iv ? t.entry_atm_iv / 100 : NaN),
      atm_iv_14d: NaN, atm_iv_30d: NaN,
      ivp_atm_7d_90d: t.ivp_atm_7d_90d_at_entry ?? NaN,
      ivp_4h: NaN,
      rv_7d: t.entry_hv ?? NaN, rv_14d: NaN,
      risk_reversal_25d: NaN, butterfly_25d: NaN, wing_atm_ratio: NaN,
      term_slope_7_30: NaN, rvp_4h: NaN, vrp_pct_7d: NaN,
      adx_14_4h: NaN, atr_pct_4h: NaN, pcr_oi: NaN,
      total_gex: NaN, gex_regime: '', expected_move_1sigma_7d: NaN,
      pattern: t.pattern ?? 'Other',
    };

    setSelectedTradeAnalytics({ legs: aLegs, ctx, calibration: null, loading: true });

    // Fetch full snapshot context + calibration in background for the richer panel
    const controller = new AbortController();
    Promise.all([
      historicalApi.getSnapshotContext(entryUnix, controller.signal).catch(() => null),
      historicalApi.getCalibration(
        dte, t.spot_at_entry ?? 0,
        Math.abs(aLegs[0]?.delta ?? 0.10),
        ctx.ivp_atm_7d_90d || 50,
        controller.signal,
      ).catch(() => null),
    ]).then(([snap, calib]) => {
      const num = (k: string): number => {
        const v = snap?.[k];
        return typeof v === 'number' ? v : NaN;
      };
      const enrichedCtx: AnalyticsContext = snap ? {
        ...ctx,
        atm_iv_7d: num('atm_iv_7d'), atm_iv_14d: num('atm_iv_14d'), atm_iv_30d: num('atm_iv_30d'),
        ivp_atm_7d_90d: num('ivp_atm_7d_90d'), ivp_4h: num('ivp_4h'),
        rv_7d: num('rv_7d'), rv_14d: num('rv_14d'),
        risk_reversal_25d: num('risk_reversal_25d'),
        butterfly_25d: num('butterfly_25d'), wing_atm_ratio: num('wing_atm_ratio'),
        term_slope_7_30: num('term_slope_7_30'),
        rvp_4h: num('rvp_4h'), vrp_pct_7d: num('vrp_pct_7d'),
        adx_14_4h: num('adx_14_4h'), atr_pct_4h: num('atr_pct_4h'),
        pcr_oi: num('pcr_oi'), total_gex: num('total_gex'),
        gex_regime: typeof snap.gex_regime === 'string' ? snap.gex_regime : '',
        expected_move_1sigma_7d: num('expected_move_1sigma_7d'),
        pattern: (typeof snap.pattern === 'string' ? snap.pattern : t.pattern) ?? 'Other',
      } : ctx;
      setSelectedTradeAnalytics({
        legs: aLegs, ctx: enrichedCtx,
        calibration: (calib as CalibrationBucket | null) ?? null,
        loading: false,
      });
    });

    return () => controller.abort();
  }, [selectedTrade]);
  const [spotOhlc, setSpotOhlc] = useState<SpotOhlcBar[]>([]);
  const [indicatorData, setIndicatorData] = useState<Record<string, IndicatorPoint[]>>({});
  const indicatorAbortRef = useRef<AbortController | null>(null);

  // Use the backtest's start date for the chart context.
  const chartWindow = useMemo(() => {
    const startDate = readPersisted<string>('backtest:startDate');
    if (!startDate) return null;
    const start = Math.floor(new Date(startDate + 'T00:00:00+05:30').getTime() / 1000);
    const end   = start + 24 * 3600 - 60;
    return { start, end };
  }, [status?.status]);  // re-evaluate when a new run completes

  useEffect(() => {
    if (!chartWindow) return;
    if (indicatorAbortRef.current) indicatorAbortRef.current.abort();
    indicatorAbortRef.current = new AbortController();
    const sig = indicatorAbortRef.current.signal;

    const ohlcP = historicalApi.getSpotOhlc(chartWindow.start, chartWindow.end, chartTimeframe, sig);
    const indP = indicatorConfigs.length === 0
      ? Promise.resolve({ indicators: {} as Record<string, IndicatorPoint[]> })
      : historicalApi.getSpotIndicators(chartWindow.start, chartWindow.end, chartTimeframe, indicatorConfigs, sig);

    Promise.all([ohlcP, indP])
      .then(([ohlcRes, indRes]) => {
        setSpotOhlc(ohlcRes.data ?? []);
        setIndicatorData(indRes.indicators ?? {});
      })
      .catch(err => {
        if (err?.name !== 'AbortError') console.error('backtest spot chart fetch failed', err);
      });

    return () => indicatorAbortRef.current?.abort();
  }, [chartWindow, chartTimeframe, indicatorConfigs]);

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

            {chartWindow && (
              <>
                <IndicatorConfigPanel
                  configs={indicatorConfigs}
                  setConfigs={setIndicatorConfigs}
                  source={chartSource}
                  setSource={setChartSource}
                  vwapAvailable={chartSource === 'spot'}
                />
                <SpotChart
                  ohlc={spotOhlc}
                  configs={indicatorConfigs}
                  indicators={indicatorData}
                  height={420}
                  premiumMode={chartSource === 'leg'}
                />
              </>
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

                <BacktestTradeLogTable
                  trades={result.trades}
                  summary={result.summary}
                  onSelectTrade={setSelectedTrade}
                  selectedTradeKey={selectedTrade
                    ? `${selectedTrade.date}|${selectedTrade.entry_time ?? ''}` : null}
                />

                {selectedTrade && selectedTradeAnalytics && (
                  <CardLight title={`Trade Analytics — ${selectedTrade.date} ${selectedTrade.entry_time ?? ''}`}>
                    <StrangleAnalyticsPanel
                      legs={selectedTradeAnalytics.legs}
                      ctx={selectedTradeAnalytics.ctx}
                      calibration={selectedTradeAnalytics.calibration}
                      loading={selectedTradeAnalytics.loading}
                      marketContext={selectedTrade.market_context ?? null}
                      title=""
                    />
                  </CardLight>
                )}
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
