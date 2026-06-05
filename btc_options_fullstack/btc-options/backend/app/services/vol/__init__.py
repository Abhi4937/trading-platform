"""Vendored realized-vol / implied-vol analysis engine.

Pure-math modules adapted from the standalone `rv_engine/` CLI tool, with two
deliberate deviations from the original:

  1. `fri_sat_filter.compute_fri_sat_stats` annualizes the trade-window vol from
     the RMS of window returns (a true sigma), NOT the median — see the note in
     that module. The original median-based number understates vol ~1.5x.
  2. `fri_sat_filter.extract_fri_sat_windows` takes an explicit `ref_time` anchor
     (the simulated timestamp) instead of `pd.Timestamp.now()`, so it works on
     historical/simulated data rather than only the live wall clock.

Greeks are NOT duplicated here — the orchestrator feeds gamma/theta from the
platform's own `app.core.greeks.compute_greeks` into `greeks_ext.gamma_theta_ratio`.
"""
