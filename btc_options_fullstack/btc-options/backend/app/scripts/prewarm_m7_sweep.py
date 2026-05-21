"""One-shot pre-warm script for M7 Sweep exit caches (Part F).

Warms the per-rule exit_cache for all delta_match rules, then emits
exit_cache/delta_match/_INDEX.json for per-rule discoverability (Part E).

Current delta_match grid: 295 rules
  = 175 premium_sl + 3 cap_sl baselines + 15 cap_sl×premium_sl
  + 30 cap_sl×max_profit + 30 cap_sl×margin_target + 42 cap_sl×exit_hr

Coverage and pivot disk-caches (Parts A and B) warm on first user request
and then persist automatically — no explicit pre-warm needed here.

Checkpoint safety: each rule is skip-if-exists, so the script can be
restarted safely after a container restart without re-computing warm rules.

RECOMMENDED — dedicated container (survives backend restarts; RULE #5):

    docker compose run -d --rm \\
        --name prewarm_$(date +%s) \\
        -v /home/abhis/btc-data/logs:/logs \\
        backend sh -c "python -m app.scripts.prewarm_m7_sweep \\
            > /logs/prewarm_$(date +%s).log 2>&1"

Monitor progress:
    docker logs -f <container_name>
    tail -f /logs/prewarm_<timestamp>.log

ETA: ~295 rules × ~2 min cold each ≈ 10 h total; incremental runs skip cached rules.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time

import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

DATASET = "delta_match"


def main() -> int:
    from app.api import m7_results as m7r
    from app.api.m7_best_combo import _rule_variants

    log.info("M7 Sweep prewarm starting — dataset=%s", DATASET)
    start_ts = time.time()

    variants = _rule_variants(DATASET)
    log.info("Rule variants to warm: %d", len(variants))

    # Pre-load trades once so all _derive_exits calls hit the in-memory cache
    try:
        trades = m7r._load_trades(DATASET)
        log.info("Trades loaded: %d rows", len(trades))
    except Exception as exc:
        log.error("Failed to load trades — aborting: %s", exc)
        return 1

    n_warm = 0
    n_skip = 0
    n_fail = 0

    for i, (label, rule_dict) in enumerate(variants, 1):
        cache_path = m7r._rule_cache_path(DATASET, rule_dict)
        if os.path.exists(cache_path):
            n_skip += 1
            if n_skip == 1 or n_skip % 25 == 0:
                log.info("[%3d/%d] %s — skip (cached)", i, len(variants), label)
            continue

        t0 = time.time()
        try:
            m7r._derive_exits({}, rule_dict, dataset=DATASET)
            elapsed = time.time() - t0
            n_warm += 1
            total_elapsed = time.time() - start_ts
            rate = n_warm / (total_elapsed / 60.0) if total_elapsed > 0 else 0.0
            remaining = len(variants) - i
            eta_min = remaining / (rate if rate > 0 else 0.01)
            log.info(
                "[%3d/%d] %-32s  %.1fs  rate %.1f/min  ETA %.0f min",
                i, len(variants), label, elapsed, rate, eta_min,
            )
        except Exception as exc:
            n_fail += 1
            log.warning("[%3d/%d] %s — FAILED: %s", i, len(variants), label, exc)

    total = time.time() - start_ts
    log.info(
        "Exit cache complete: warmed=%d  skipped=%d  failed=%d  total_time=%.1f min",
        n_warm, n_skip, n_fail, total / 60.0,
    )

    # Part E: emit per-rule index file
    _emit_index(variants, DATASET, m7r)

    log.info("Prewarm finished in %.1f min", (time.time() - start_ts) / 60.0)
    return 0 if n_fail == 0 else 1


def _emit_index(
    variants: list[tuple[str, dict]],
    dataset: str,
    m7r,
) -> None:
    """Write exit_cache/<dataset>/_INDEX.json mapping rule_label → sha1 + stats."""
    index_dir = os.path.join(m7r.M7_BASE_DIR, "exit_cache", dataset)
    os.makedirs(index_dir, exist_ok=True)
    index_path = os.path.join(index_dir, "_INDEX.json")

    _, trades_mtime = m7r._TRADES_BY_DATASET.get(dataset, (None, 0.0))

    rules: list[dict] = []
    for label, rule_dict in variants:
        cache_path = m7r._rule_cache_path(dataset, rule_dict)
        sha1 = os.path.basename(cache_path).replace(".parquet", "")
        entry: dict = {"rule_label": label, "sha1": sha1}
        if os.path.exists(cache_path):
            try:
                size_mb = round(os.path.getsize(cache_path) / 1024 / 1024, 1)
                df = pd.read_parquet(cache_path)
                n_trades = len(df)
                n_fallback = int(df["is_fallback"].sum()) if "is_fallback" in df.columns else 0
                entry["n_trades"] = n_trades
                entry["n_fallback"] = n_fallback
                entry["file_size_mb"] = size_mb
            except Exception:
                entry["n_trades"] = None
                entry["n_fallback"] = None
                entry["file_size_mb"] = None
        rules.append(entry)

    index = {
        "dataset": dataset,
        "trades_mtime": trades_mtime,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "n_rules": len(rules),
        "rules": rules,
    }
    tmp = index_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(index, f, indent=2)
    os.replace(tmp, index_path)
    log.info("INDEX written: %s (%d rules)", index_path, len(rules))


if __name__ == "__main__":
    sys.exit(main())
