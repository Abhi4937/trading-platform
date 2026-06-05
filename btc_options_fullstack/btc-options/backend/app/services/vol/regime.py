"""
Regime detection from RV estimator spreads.

The KEY insight: the spread between different estimators reveals the
KIND of vol BTC is exhibiting, not just how much.

  - Parkinson >> Close-Open  →  intraday chop (range vol overweights swings)
  - Parkinson ≈ Close-Open   →  clean trend (range = direction)
  - Rogers-Satchell >> GK    →  strong drift, GK is suppressed
  - All estimators converge  →  honest regime, signals trustworthy

This module classifies the regime and recommends WHICH RV to use for
the IV/RV ratio (auto-selection).

Vendored verbatim from rv_engine/regime.py (only the config import path changed).
"""

from dataclasses import dataclass
import numpy as np

from .constants import (
    CHOP_RATIO_LOW,
    CHOP_RATIO_HIGH,
    TREND_INTENSITY_THRESHOLD,
)


@dataclass
class RegimeRead:
    """Container for regime detection output."""
    chop_ratio: float          # Parkinson / Close-Open
    trend_intensity: float     # (RS - GK) / GK
    chop_label: str            # "trend" / "mixed" / "chop"
    trend_label: str           # "no_trend" / "trend" / "strong_trend"
    recommended_estimator: str  # which RV to use for ratio
    rationale: str             # plain-English explanation

    def __str__(self) -> str:
        return (
            f"Chop ratio: {self.chop_ratio:.2f} ({self.chop_label})\n"
            f"Trend intensity: {self.trend_intensity*100:.1f}% ({self.trend_label})\n"
            f"Recommended estimator for IV/RV: {self.recommended_estimator}\n"
            f"Rationale: {self.rationale}"
        )


def detect_regime(rv_dict: dict) -> RegimeRead:
    """
    Classify the current vol regime from estimator outputs.

    Decision tree:
      Step 1 — Chop ratio (Parkinson / Close-Open)
        > 3.0  → CHOP regime → use Parkinson for ratio
        < 1.5  → TREND regime → check RS vs GK next
        else   → MIXED → standard estimators OK

      Step 2 — Trend intensity (RS - GK) / GK
        > 0.30  → STRONG TREND → use Rogers-Satchell (drift-robust)
        else    → normal trend behavior → use Close-to-Close
    """
    p = rv_dict.get("parkinson", np.nan)
    co = rv_dict.get("close_open", np.nan)
    gk = rv_dict.get("garman_klass", np.nan)
    rs = rv_dict.get("rogers_satchell", np.nan)

    # Chop ratio
    if co > 0 and not np.isnan(p) and not np.isnan(co):
        chop_ratio = p / co
    else:
        chop_ratio = np.nan

    # Trend intensity
    if gk > 0 and not np.isnan(rs) and not np.isnan(gk):
        trend_intensity = (rs - gk) / gk
    else:
        trend_intensity = np.nan

    # Classify chop
    if np.isnan(chop_ratio):
        chop_label = "unknown"
    elif chop_ratio > CHOP_RATIO_HIGH:
        chop_label = "chop"
    elif chop_ratio < CHOP_RATIO_LOW:
        chop_label = "trend"
    else:
        chop_label = "mixed"

    # Classify trend
    if np.isnan(trend_intensity):
        trend_label = "unknown"
    elif trend_intensity > TREND_INTENSITY_THRESHOLD:
        trend_label = "strong_trend"
    elif trend_intensity > 0.10:
        trend_label = "trend"
    else:
        trend_label = "no_trend"

    # Recommend estimator
    recommended, rationale = _recommend_estimator(chop_label, trend_label)

    return RegimeRead(
        chop_ratio=chop_ratio if not np.isnan(chop_ratio) else 0.0,
        trend_intensity=trend_intensity if not np.isnan(trend_intensity) else 0.0,
        chop_label=chop_label,
        trend_label=trend_label,
        recommended_estimator=recommended,
        rationale=rationale,
    )


def _recommend_estimator(chop_label: str, trend_label: str) -> tuple[str, str]:
    """
    Pick the right RV estimator for the IV/RV ratio given the regime.
    """
    if trend_label == "strong_trend":
        return (
            "rogers_satchell",
            "Strong directional drift detected — GK is suppressed by trend. "
            "Use Rogers-Satchell (drift-robust) for honest vol measurement. "
            "WARNING: don't sell premium in trending markets."
        )
    if chop_label == "chop":
        return (
            "parkinson",
            "Heavy intraday chop — Close-based estimators understate vol. "
            "Use Parkinson (range-based) which captures swing magnitude. "
            "Premium selling favored, but use range-based stops."
        )
    if chop_label == "trend":
        return (
            "close_to_close",
            "Clean trending behavior — direction matches range, no chop. "
            "CC is appropriate. Directional plays favored over premium-only."
        )
    # mixed / default
    return (
        "close_to_close",
        "Balanced regime — estimators agree. CC is the standard, "
        "most directly comparable to IV. All signals trustworthy."
    )


def trend_efficiency_label(trend_eff_median: float) -> str:
    """Translate trend efficiency median into a label."""
    if np.isnan(trend_eff_median):
        return "unknown"
    if trend_eff_median > 0.70:
        return "trending (high efficiency)"
    if trend_eff_median > 0.50:
        return "directional with retracement"
    if trend_eff_median > 0.30:
        return "mixed"
    return "ranging (low efficiency)"
