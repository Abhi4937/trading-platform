"""
Backend re-export of the v2 margin engine.

The engine itself is `app/services/margin_engine_v2.py` — a copy of the
calibrated standalone tool in `scripts/margin_engine_v2.py`. We keep this
shim so callers can `from app.services.margin_v2 import ...` without
worrying about the engine's import path. Update both files in lockstep
when recalibrating.
"""

from __future__ import annotations

from .margin_engine_v2 import (  # noqa: F401
    MarginLeg,
    MarginResult,
    compute_portfolio_margin,
    CONTRACT_VALUE,
)

__all__ = ["MarginLeg", "MarginResult", "compute_portfolio_margin", "CONTRACT_VALUE"]
