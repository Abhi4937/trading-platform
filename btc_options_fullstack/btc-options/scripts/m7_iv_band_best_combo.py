"""M7 — IV-band best-combo offline xlsx export.

Calls the FastAPI endpoint twice (ranking=credit, ranking=margin) and once
more with include_grid=true to dump the full 11,760-cell grid. Polls the
backend if the warmup is still in progress (returns within ~10–15 min cold,
near-instant when warm).

Output: scripts/m7_iv_band_best_combo.xlsx with three sheets:
  - best_credit  (10 rows — winners ranked by avg % return on credit)
  - best_margin  (10 rows — winners ranked by avg % return on margin)
  - raw          (full 11,760-cell grid for drill-down)
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

import pandas as pd


BASE = os.environ.get("M7_BC_BASE", "http://localhost:8000")
ENDPOINT = f"{BASE}/api/v1/m7/iv_band_best_combo"
STATUS_ENDPOINT = f"{ENDPOINT}/status"
OUTPUT_PATH = Path(__file__).resolve().parent / "m7_iv_band_best_combo.xlsx"

POLL_SECONDS = 5
MAX_WAIT_SECONDS = 60 * 30  # 30 min ceiling; warmup is ~5–10 min cold


def _get_json(url: str, timeout: float = 30.0) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _wait_for_warm(verbose: bool = True) -> None:
    """Block until /status reports `ready` (or fails)."""
    start = time.time()
    last_progress = -1
    while True:
        try:
            s = _get_json(STATUS_ENDPOINT, timeout=5.0)
        except (urllib.error.URLError, TimeoutError) as exc:
            if verbose:
                print(f"  status probe failed ({exc}); retrying in {POLL_SECONDS}s",
                      file=sys.stderr)
            time.sleep(POLL_SECONDS)
            continue

        status = s.get("status")
        done = int(s.get("rules_done", 0))
        total = int(s.get("rules_total", 0))
        if status == "ready":
            if verbose:
                print(f"  warmup READY ({done}/{total} rules cached)")
            return
        if status == "error":
            raise RuntimeError(f"backend warmup failed: {s.get('error')}")
        if verbose and done != last_progress:
            elapsed = int(time.time() - start)
            print(f"  warming… {done}/{total} rules cached  (elapsed {elapsed}s)")
            last_progress = done
        if time.time() - start > MAX_WAIT_SECONDS:
            raise TimeoutError(
                f"warmup did not complete within {MAX_WAIT_SECONDS}s "
                f"(reached {done}/{total} rules)"
            )
        time.sleep(POLL_SECONDS)


def _fetch(ranking: str, include_grid: bool = False) -> dict:
    url = f"{ENDPOINT}?ranking={ranking}"
    if include_grid:
        url += "&include_grid=true"
    return _get_json(url, timeout=120.0)


def _rule_label(rule: dict) -> str:
    """Normalise rule dict → label (mirrors backend formatter)."""
    if rule.get("max_profit_pct") is not None:
        return f"max_profit_{int(rule['max_profit_pct'])}"
    if rule.get("margin_target_pct") is not None:
        return f"margin_target_{int(rule['margin_target_pct'])}"
    return "baseline_sl100"


def _flatten_rows(rows: list[dict]) -> pd.DataFrame:
    """Convert list-of-dict response rows into a flat DataFrame, expanding
    the nested `rule` dict into columns."""
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    if "rule" in df.columns:
        df["rule_premium_sl_pct"] = df["rule"].apply(
            lambda r: (r or {}).get("premium_sl_pct"))
        df["rule_max_profit_pct"] = df["rule"].apply(
            lambda r: (r or {}).get("max_profit_pct"))
        df["rule_margin_target_pct"] = df["rule"].apply(
            lambda r: (r or {}).get("margin_target_pct"))
        df = df.drop(columns=["rule"])
    return df


def main() -> int:
    print(f"Calling status endpoint: {STATUS_ENDPOINT}")
    s = _get_json(STATUS_ENDPOINT, timeout=10.0)
    print(f"  initial status: {s}")
    if s.get("status") != "ready":
        # Hitting the main endpoint kicks off warmup if not started; polling
        # status afterwards reports the live progress.
        try:
            _fetch("credit")  # may return warming payload — we don't care here
        except Exception:
            pass
        _wait_for_warm()

    print("Fetching ranking=credit …")
    rcred = _fetch("credit")
    print(f"  {len(rcred.get('rows', []))} rows")
    print("Fetching ranking=margin …")
    rmarg = _fetch("margin")
    print(f"  {len(rmarg.get('rows', []))} rows")
    print("Fetching full grid …")
    rgrid = _fetch("credit", include_grid=True)
    grid_rows = rgrid.get("grid") or []
    print(f"  {len(grid_rows)} grid cells")

    df_credit = _flatten_rows(rcred.get("rows", []))
    df_margin = _flatten_rows(rmarg.get("rows", []))
    df_grid = _flatten_rows(grid_rows)

    if df_grid.empty:
        # Defensive — endpoint returned empty grid even after warmup
        print("WARNING: full grid is empty; xlsx will only have summary sheets.",
              file=sys.stderr)

    print(f"Writing {OUTPUT_PATH} …")
    with pd.ExcelWriter(OUTPUT_PATH, engine="openpyxl") as xl:
        if not df_credit.empty:
            df_credit.to_excel(xl, sheet_name="best_credit", index=False)
        if not df_margin.empty:
            df_margin.to_excel(xl, sheet_name="best_margin", index=False)
        if not df_grid.empty:
            df_grid.to_excel(xl, sheet_name="raw", index=False)
    print(f"  wrote {OUTPUT_PATH}  "
          f"({df_credit.shape[0]} / {df_margin.shape[0]} / {df_grid.shape[0]} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
