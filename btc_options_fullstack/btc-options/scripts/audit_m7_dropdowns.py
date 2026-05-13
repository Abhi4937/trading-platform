"""Audit: do all the Best Combo dropdown options actually return data?

User reported some dropdown picks (e.g. "Total" / sum_net_pnl) show no
data. This script sweeps every key in the frontend's PRIMARY_GROUPS and
SECONDARY_GROUPS, fires the live endpoint with each as the ranking /
secondary / dd_metric, and catalogues what fails.

Read-only — all requests are GET. No system mutation.

Run from repo root:
    python3 scripts/audit_m7_dropdowns.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from urllib.parse import urlencode

try:
    import httpx
except ImportError:
    import urllib.request
    import json
    httpx = None

BASE = "http://localhost:8000/api/v1/m7/iv_band_best_combo"
TIMEOUT = 30.0
REPO_ROOT = Path(__file__).resolve().parent.parent
OUT = REPO_ROOT / "docs" / "m7_dropdown_coverage_audit.md"


# Exact lists mirrored from M7IvBandBestComboTable.tsx
PRIMARY: list[tuple[str, str]] = [
    # (group_label, key)
    ("Composite", "composite_score"),
    ("Composite", "sharpe_per_trade"),
    ("Composite", "sortino_per_trade"),
    ("Composite", "calmar_like"),
    ("P&L", "avg_net_pnl"),
    ("P&L", "sum_net_pnl"),
    ("P&L", "avg_win_usd"),
    ("P&L", "avg_loss_usd"),
    ("P&L", "max_win_usd"),
    ("P&L", "max_loss_usd"),
    ("P&L", "total_win_mtm"),
    ("P&L", "total_loss_mtm"),
    ("% return", "avg_pct_return_on_credit"),
    ("% return", "avg_pct_return_on_margin"),
    ("% return", "avg_pct_return_on_credit_winners"),
    ("% return", "avg_pct_return_on_margin_winners"),
    ("% return", "avg_pct_max_mtm_on_credit"),
    ("% return", "avg_pct_min_mtm_on_credit"),
    ("Risk", "avg_min_mtm_losers"),
    ("Risk", "avg_min_mtm_winners"),
    ("Risk", "max_consec_losses"),
    ("Risk", "max_consec_sl_hits"),
    ("Win counts", "win_rate"),
    ("Win counts", "n_wins"),
    ("Win counts", "n_losses"),
    ("Win counts", "n_trades"),
]

SECONDARY: list[tuple[str, str]] = [
    ("Loss magnitude", "avg_loss_usd"),
    ("Loss magnitude", "max_loss_usd"),
    ("Loss magnitude", "total_loss_mtm"),
    ("Loss magnitude", "avg_loss_mtm"),
    ("Loss magnitude", "largest_loss_mtm"),
    ("DD (losers)", "avg_min_mtm_losers"),
    ("DD (losers)", "min_mtm_losers"),
    ("DD (losers)", "avg_max_mtm_losers"),
    ("DD (losers)", "avg_pct_min_mtm_on_credit"),
    ("DD (winners)", "avg_min_mtm_winners"),
    ("DD (winners)", "min_mtm_winners"),
    ("DD (overall)", "avg_min_mtm"),
    ("DD (overall)", "min_mtm"),
    ("Frequency", "n_losses"),
    ("Frequency", "n_premium_sl_hit"),
    ("Frequency", "n_rule_trigger"),
    ("Frequency", "n_hard_cap"),
    ("Streaks", "max_consec_losses"),
    ("Streaks", "max_consec_sl_hits"),
    ("Streaks", "max_consec_premium_sl_hits"),
    ("Behavioral", "n_losers_above_avg_max_mtm"),
    ("Behavioral", "avg_loser_exit_offset_minutes"),
]


def _get(params: dict) -> dict:
    """GET with httpx if available, else urllib."""
    qs = urlencode({k: v for k, v in params.items() if v is not None})
    url = f"{BASE}?{qs}"
    if httpx is not None:
        try:
            r = httpx.get(url, timeout=TIMEOUT)
            return {"status_code": r.status_code, "body": r.json() if r.headers.get("content-type", "").startswith("application/json") else {"raw": r.text[:500]}}
        except Exception as e:
            return {"status_code": -1, "body": {"error": str(e)}}
    import json as _json
    import urllib.request as _urllib
    try:
        with _urllib.urlopen(url, timeout=TIMEOUT) as resp:
            return {"status_code": resp.status, "body": _json.load(resp)}
    except Exception as e:
        return {"status_code": -1, "body": {"error": str(e)}}


def _verdict(result: dict, key: str, role: str) -> tuple[str, str]:
    """Return (verdict_emoji, short_reason)."""
    sc = result["status_code"]
    body = result.get("body", {})
    if sc == 400:
        return ("FAIL-400", f"backend rejected: {body.get('detail', '')[:80]}")
    if sc != 200:
        return ("FAIL", f"http {sc} — {str(body)[:80]}")
    status = body.get("status")
    if status == "warming":
        return ("WARM", "grid still warming")
    if status not in ("ready",):
        return ("FAIL", f"status={status}")
    rows = body.get("rows") or []
    n_cells = body.get("n_cells", 0)
    if not rows:
        return ("EMPTY", f"rows=0, n_cells={n_cells}")
    # Count rows where the key is non-null. If 0/N have data, it's broken.
    # If 1+ have data, the metric works — null cells just reflect bands where
    # the metric is genuinely undefined (e.g. avg_loss_usd in a zero-loss band).
    if role in ("primary", "secondary"):
        non_null = sum(1 for r in rows if isinstance(r, dict) and r.get(key) is not None)
        if non_null == 0:
            return ("NULL", f"all {len(rows)} rows null; n_cells={n_cells}")
        if non_null < len(rows):
            return ("OK*", f"rows={len(rows)} ({len(rows)-non_null} null); n_cells={n_cells}")
    return ("OK", f"rows={len(rows)} n_cells={n_cells}")


def sweep_primary() -> list[dict]:
    out = []
    for group, key in PRIMARY:
        r = _get({"ranking": key, "min_hit_pct": 0, "min_n_trades": 0})
        v, why = _verdict(r, key, "primary")
        rows = r.get("body", {}).get("rows") or []
        first_val = None
        if rows and isinstance(rows[0], dict):
            first_val = rows[0].get(key)
        out.append({
            "role": "primary", "group": group, "key": key,
            "status_code": r["status_code"],
            "rows": len(rows),
            "n_cells": r.get("body", {}).get("n_cells", 0),
            "first_val": first_val,
            "verdict": v, "reason": why,
        })
    return out


def sweep_secondary() -> list[dict]:
    out = []
    for group, key in SECONDARY:
        r = _get({
            "ranking": "avg_net_pnl",
            "secondary": key,
            "tolerance_pct": 5.0,
            "min_hit_pct": 0,
            "min_n_trades": 0,
        })
        v, why = _verdict(r, key, "secondary")
        rows = r.get("body", {}).get("rows") or []
        first_val = None
        if rows and isinstance(rows[0], dict):
            first_val = rows[0].get(key)
        out.append({
            "role": "secondary", "group": group, "key": key,
            "status_code": r["status_code"],
            "rows": len(rows),
            "n_cells": r.get("body", {}).get("n_cells", 0),
            "first_val": first_val,
            "verdict": v, "reason": why,
        })
    return out


def sweep_dd_cap() -> list[dict]:
    """DD-cap uses SECONDARY metric set as the constraint metric."""
    out = []
    for group, key in SECONDARY:
        # Use a generous threshold (1e6) so the constraint never binds and
        # we're only testing whether the endpoint accepts the key.
        r = _get({
            "ranking": "avg_net_pnl",
            "total_capital_usd": 600,
            "pct_deploy": 100,
            "dd_metric": key,
            "dd_threshold": 1_000_000,
            "min_hit_pct": 0,
            "min_n_trades": 0,
        })
        v, why = _verdict(r, key, "dd")
        rows = r.get("body", {}).get("rows") or []
        out.append({
            "role": "dd_metric", "group": group, "key": key,
            "status_code": r["status_code"],
            "rows": len(rows),
            "n_cells": r.get("body", {}).get("n_cells", 0),
            "first_val": None,
            "verdict": v, "reason": why,
        })
    return out


def sweep_toggles() -> list[dict]:
    out = []
    for rf in ("all", "max_profit", "margin_target"):
        for sm in ("capital", "lots"):
            for pm in ("by_hour", "aggregate_hours"):
                params = {
                    "ranking": "avg_net_pnl",
                    "rule_family": rf,
                    "pick_mode": pm,
                    "min_hit_pct": 0,
                    "min_n_trades": 0,
                }
                if sm == "capital":
                    params["total_capital_usd"] = 600
                    params["pct_deploy"] = 100
                r = _get(params)
                rows = r.get("body", {}).get("rows") or []
                v = "OK" if (r["status_code"] == 200 and rows) else "FAIL"
                out.append({
                    "rule_family": rf, "sizing_mode": sm, "pick_mode": pm,
                    "status_code": r["status_code"],
                    "rows": len(rows),
                    "n_cells": r.get("body", {}).get("n_cells", 0),
                    "verdict": v,
                })
    return out


def _fmt_val(v):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.3g}"
    return str(v)


def write_report(primary_results, secondary_results, dd_results, toggle_results):
    lines = ["# M7 Best Combo — Dropdown Coverage Audit\n"]
    lines.append(
        "\nFor every metric key in the Best Combo table's Primary / "
        "Secondary / DD-cap dropdowns, hit the live endpoint and check that "
        "the response actually carries data for that key. All requests use "
        "`min_hit_pct=0&min_n_trades=0` so picker filters don't mask "
        "structural issues.\n"
    )

    def _table(rows, role_col=False):
        head = "| Group | Key | HTTP | rows | n_cells | First row | Verdict | Why |"
        sep = "|---|---|---|---|---|---|---|---|"
        out = [head, sep]
        for r in rows:
            out.append(
                f"| {r['group']} | `{r['key']}` | {r['status_code']} | "
                f"{r['rows']} | {r['n_cells']} | {_fmt_val(r['first_val'])} | "
                f"{r['verdict']} | {r['reason']} |"
            )
        return "\n".join(out)

    lines.append("\n## Table 1 — Primary metric sweep\n")
    lines.append(_table(primary_results))

    lines.append("\n\n## Table 2 — Secondary (tiebreak) metric sweep\n")
    lines.append(_table(secondary_results))

    lines.append("\n\n## Table 3 — DD-cap metric sweep\n")
    lines.append(_table(dd_results))

    lines.append("\n\n## Table 4 — Toggle combinations (rule_family × sizing_mode × pick_mode)\n")
    head = "| rule_family | sizing_mode | pick_mode | HTTP | rows | n_cells | Verdict |"
    sep = "|---|---|---|---|---|---|---|"
    lines.append(head)
    lines.append(sep)
    for r in toggle_results:
        lines.append(
            f"| {r['rule_family']} | {r['sizing_mode']} | {r['pick_mode']} | "
            f"{r['status_code']} | {r['rows']} | {r['n_cells']} | {r['verdict']} |"
        )

    # Broken summary
    def _broken(rows):
        return [r for r in rows if r["verdict"] not in ("OK", "OK*", "WARM")]

    bp = _broken(primary_results)
    bs = _broken(secondary_results)
    bd = _broken(dd_results)
    bt = [r for r in toggle_results if r["verdict"] != "OK"]
    lines.append("\n\n## Summary\n")
    lines.append(
        f"- Primary:   {len(primary_results) - len(bp)} / {len(primary_results)} OK\n"
        f"- Secondary: {len(secondary_results) - len(bs)} / {len(secondary_results)} OK\n"
        f"- DD-cap:    {len(dd_results) - len(bd)} / {len(dd_results)} OK\n"
        f"- Toggles:   {len(toggle_results) - len(bt)} / {len(toggle_results)} OK\n"
    )
    if bp or bs or bd or bt:
        lines.append("\n### Broken keys\n")
        for r in bp + bs + bd:
            lines.append(f"- **{r['role']}** `{r['key']}` ({r['group']}): {r['verdict']} — {r['reason']}")
        for r in bt:
            lines.append(f"- **toggle** {r['rule_family']}/{r['sizing_mode']}/{r['pick_mode']}: {r['verdict']}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines))


def main() -> int:
    t0 = time.time()
    print("sweeping primary metrics...", file=sys.stderr)
    p = sweep_primary()
    print("sweeping secondary metrics...", file=sys.stderr)
    s = sweep_secondary()
    print("sweeping dd-cap metrics...", file=sys.stderr)
    d = sweep_dd_cap()
    print("sweeping toggle combinations...", file=sys.stderr)
    t = sweep_toggles()
    write_report(p, s, d, t)
    elapsed = time.time() - t0

    # One-line verdict to stdout
    ok = lambda rs: sum(1 for r in rs if r["verdict"] in ("OK", "OK*", "WARM"))
    print(
        f"Dropdown audit ({elapsed:.1f}s): "
        f"primary {ok(p)}/{len(p)}, secondary {ok(s)}/{len(s)}, "
        f"dd-cap {ok(d)}/{len(d)}, toggles "
        f"{sum(1 for r in t if r['verdict'] == 'OK')}/{len(t)}. "
        f"Report: {OUT}"
    )
    broken_primary = [r for r in p if r["verdict"] not in ("OK", "OK*", "WARM")]
    if broken_primary:
        print("Broken primary keys:")
        for r in broken_primary:
            print(f"  • {r['key']} ({r['group']}): {r['verdict']} — {r['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
