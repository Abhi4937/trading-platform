"""Configuration constants for the vol engine.

Subset of the standalone `rv_engine/config.py` — the API/endpoint constants are
deliberately dropped (this package never talks to an exchange; it runs on the
platform's own parquet data and greeks).
"""

# ============================================================
# LOOKBACK WINDOWS (in days)
# ============================================================
LOOKBACK_WINDOWS = [4, 7, 14, 30]
PRIMARY_LOOKBACK = 14            # For IV/RV ratio
SL_LOOKBACK = 4                  # For stop-loss placement
PERCENTILE_LOOKBACK = 60         # For percentile rank of IV/RV ratio
FRI_SAT_WEEKS = 12               # Fri-Sat regime filter

# ============================================================
# RV ESTIMATORS — order matters (first is the default/headline)
# ============================================================
RV_ESTIMATORS = [
    "close_to_close",   # CC - textbook standard
    "close_open",       # CO - single-session directional
    "parkinson",        # P - range-based
    "garman_klass",     # GK - full OHLC, drift-free assumption
    "rogers_satchell",  # RS - drift-robust
]

# ============================================================
# REGIME DETECTION THRESHOLDS
# ============================================================
CHOP_RATIO_LOW = 1.5     # Below this = clean trend
CHOP_RATIO_HIGH = 3.0    # Above this = heavy chop
TREND_INTENSITY_THRESHOLD = 0.30   # (RS-GK)/GK > 30% = strong trend
TERM_STRUCTURE_SLOPE_THRESHOLD = 0.05  # 5% diff between short/long = signal

# ============================================================
# IV/RV RATIO SIGNAL THRESHOLDS
# ============================================================
RATIO_RICH = 1.50           # Above this = sell premium signal
RATIO_FAIR_UPPER = 1.30
RATIO_FAIR_LOWER = 0.90
RATIO_CHEAP = 0.80          # Below this = buy premium signal

PERCENTILE_RICH = 75
PERCENTILE_CHEAP = 25

# ============================================================
# STOP-LOSS RULES (multipliers on range stdev)
# ============================================================
SL_TIGHT_MULT = 0.0      # median + 0*stdev
SL_MODERATE_MULT = 1.0   # median + 1*stdev
SL_CONSERVATIVE_MULT = 2.0  # median + 2*stdev

# ============================================================
# TRADE WINDOW DEFAULTS (Friday-Saturday strategy)
# ============================================================
DEFAULT_ENTRY_HOUR_UTC = 16   # ~21:30 IST = 16:00 UTC (Fri 10pm IST)
DEFAULT_EXIT_HOUR_UTC = 4     # ~09:30 IST Sat morning = 04:00 UTC
DEFAULT_HOLD_HOURS = 12

# ============================================================
# ANNUALIZATION
# ============================================================
DAYS_PER_YEAR = 365            # Crypto trades 24/7, so 365 (not 252)
HOURS_PER_YEAR = 365 * 24
MINUTES_PER_YEAR = 365 * 24 * 60

# ============================================================
# OPTIONS — risk-free rate. The platform prices options & solves IV at r=0
# (see app/api/historical.py); keep this consistent so gamma/theta line up
# with the option chain's greeks.
# ============================================================
RISK_FREE_RATE = 0.0
