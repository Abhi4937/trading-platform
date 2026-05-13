# M7 Best Combo — Dropdown Coverage Audit


For every metric key in the Best Combo table's Primary / Secondary / DD-cap dropdowns, hit the live endpoint and check that the response actually carries data for that key. All requests use `min_hit_pct=0&min_n_trades=0` so picker filters don't mask structural issues.


## Table 1 — Primary metric sweep

| Group | Key | HTTP | rows | n_cells | First row | Verdict | Why |
|---|---|---|---|---|---|---|---|
| Composite | `composite_score` | 200 | 10 | 206016 | 0.497 | OK | rows=10 n_cells=206016 |
| Composite | `sharpe_per_trade` | 200 | 7 | 206016 | 410 | OK | rows=7 n_cells=206016 |
| Composite | `sortino_per_trade` | 200 | 7 | 206016 | 66.2 | OK | rows=7 n_cells=206016 |
| Composite | `calmar_like` | 200 | 9 | 206016 | 429 | OK | rows=9 n_cells=206016 |
| P&L | `avg_net_pnl` | 200 | 10 | 206016 | 28.6 | OK | rows=10 n_cells=206016 |
| P&L | `sum_net_pnl` | 200 | 10 | 206016 | 226 | OK | rows=10 n_cells=206016 |
| P&L | `avg_win_usd` | 200 | 10 | 206016 | 68.9 | OK | rows=10 n_cells=206016 |
| P&L | `avg_loss_usd` | 200 | 9 | 206016 | -0.014 | OK | rows=9 n_cells=206016 |
| P&L | `max_win_usd` | 200 | 10 | 206016 | 68.9 | OK | rows=10 n_cells=206016 |
| P&L | `max_loss_usd` | 200 | 9 | 206016 | -0.014 | OK | rows=9 n_cells=206016 |
| P&L | `total_win_mtm` | 200 | 10 | 206016 | 456 | OK | rows=10 n_cells=206016 |
| P&L | `total_loss_mtm` | 200 | 10 | 206016 | 73.3 | OK | rows=10 n_cells=206016 |
| % return | `avg_pct_return_on_credit` | 200 | 10 | 206016 | 0.513 | OK | rows=10 n_cells=206016 |
| % return | `avg_pct_return_on_margin` | 200 | 10 | 206016 | 0.156 | OK | rows=10 n_cells=206016 |
| % return | `avg_pct_return_on_credit_winners` | 200 | 10 | 206016 | 0.921 | OK | rows=10 n_cells=206016 |
| % return | `avg_pct_return_on_margin_winners` | 200 | 10 | 206016 | 0.442 | OK | rows=10 n_cells=206016 |
| % return | `avg_pct_max_mtm_on_credit` | 200 | 10 | 206016 | 0.693 | OK | rows=10 n_cells=206016 |
| % return | `avg_pct_min_mtm_on_credit` | 200 | 10 | 206016 | -0.0224 | OK | rows=10 n_cells=206016 |
| Risk | `avg_min_mtm_losers` | 200 | 9 | 206016 | -0.99 | OK | rows=9 n_cells=206016 |
| Risk | `avg_min_mtm_winners` | 200 | 10 | 206016 | -0.97 | OK | rows=10 n_cells=206016 |
| Risk | `max_consec_losses` | 200 | 10 | 206016 | 0 | OK | rows=10 n_cells=206016 |
| Risk | `max_consec_sl_hits` | 200 | 10 | 206016 | 0 | OK | rows=10 n_cells=206016 |
| Win counts | `win_rate` | 200 | 10 | 206016 | 1 | OK | rows=10 n_cells=206016 |
| Win counts | `n_wins` | 200 | 10 | 206016 | 30 | OK | rows=10 n_cells=206016 |
| Win counts | `n_losses` | 200 | 10 | 206016 | 0 | OK | rows=10 n_cells=206016 |
| Win counts | `n_trades` | 200 | 10 | 206016 | 34 | OK | rows=10 n_cells=206016 |


## Table 2 — Secondary (tiebreak) metric sweep

| Group | Key | HTTP | rows | n_cells | First row | Verdict | Why |
|---|---|---|---|---|---|---|---|
| Loss magnitude | `avg_loss_usd` | 200 | 10 | 206016 | — | OK* | rows=10 (5 null); n_cells=206016 |
| Loss magnitude | `max_loss_usd` | 200 | 10 | 206016 | — | OK* | rows=10 (5 null); n_cells=206016 |
| Loss magnitude | `total_loss_mtm` | 200 | 10 | 206016 | 0 | OK | rows=10 n_cells=206016 |
| Loss magnitude | `avg_loss_mtm` | 200 | 10 | 206016 | — | OK* | rows=10 (5 null); n_cells=206016 |
| Loss magnitude | `largest_loss_mtm` | 200 | 10 | 206016 | — | OK* | rows=10 (5 null); n_cells=206016 |
| DD (losers) | `avg_min_mtm_losers` | 200 | 10 | 206016 | — | OK* | rows=10 (5 null); n_cells=206016 |
| DD (losers) | `min_mtm_losers` | 200 | 10 | 206016 | — | OK* | rows=10 (5 null); n_cells=206016 |
| DD (losers) | `avg_max_mtm_losers` | 200 | 10 | 206016 | — | OK* | rows=10 (5 null); n_cells=206016 |
| DD (losers) | `avg_pct_min_mtm_on_credit` | 200 | 10 | 206016 | -0.0758 | OK | rows=10 n_cells=206016 |
| DD (winners) | `avg_min_mtm_winners` | 200 | 10 | 206016 | -8.01 | OK | rows=10 n_cells=206016 |
| DD (winners) | `min_mtm_winners` | 200 | 10 | 206016 | -13.9 | OK | rows=10 n_cells=206016 |
| DD (overall) | `avg_min_mtm` | 200 | 10 | 206016 | -8.01 | OK | rows=10 n_cells=206016 |
| DD (overall) | `min_mtm` | 200 | 10 | 206016 | -13.9 | OK | rows=10 n_cells=206016 |
| Frequency | `n_losses` | 200 | 10 | 206016 | 0 | OK | rows=10 n_cells=206016 |
| Frequency | `n_premium_sl_hit` | 200 | 10 | 206016 | 0 | OK | rows=10 n_cells=206016 |
| Frequency | `n_rule_trigger` | 200 | 10 | 206016 | 2 | OK | rows=10 n_cells=206016 |
| Frequency | `n_hard_cap` | 200 | 10 | 206016 | 1 | OK | rows=10 n_cells=206016 |
| Streaks | `max_consec_losses` | 200 | 10 | 206016 | 0 | OK | rows=10 n_cells=206016 |
| Streaks | `max_consec_sl_hits` | 200 | 10 | 206016 | 1 | OK | rows=10 n_cells=206016 |
| Streaks | `max_consec_premium_sl_hits` | 200 | 10 | 206016 | 0 | OK | rows=10 n_cells=206016 |
| Behavioral | `n_losers_above_avg_max_mtm` | 200 | 10 | 206016 | 0 | OK | rows=10 n_cells=206016 |
| Behavioral | `avg_loser_exit_offset_minutes` | 200 | 10 | 206016 | — | OK* | rows=10 (5 null); n_cells=206016 |


## Table 3 — DD-cap metric sweep

| Group | Key | HTTP | rows | n_cells | First row | Verdict | Why |
|---|---|---|---|---|---|---|---|
| Loss magnitude | `avg_loss_usd` | 200 | 10 | 206016 | — | OK | rows=10 n_cells=206016 |
| Loss magnitude | `max_loss_usd` | 200 | 10 | 206016 | — | OK | rows=10 n_cells=206016 |
| Loss magnitude | `total_loss_mtm` | 200 | 10 | 206016 | — | OK | rows=10 n_cells=206016 |
| Loss magnitude | `avg_loss_mtm` | 200 | 10 | 206016 | — | OK | rows=10 n_cells=206016 |
| Loss magnitude | `largest_loss_mtm` | 200 | 10 | 206016 | — | OK | rows=10 n_cells=206016 |
| DD (losers) | `avg_min_mtm_losers` | 200 | 10 | 206016 | — | OK | rows=10 n_cells=206016 |
| DD (losers) | `min_mtm_losers` | 200 | 10 | 206016 | — | OK | rows=10 n_cells=206016 |
| DD (losers) | `avg_max_mtm_losers` | 200 | 10 | 206016 | — | OK | rows=10 n_cells=206016 |
| DD (losers) | `avg_pct_min_mtm_on_credit` | 200 | 10 | 206016 | — | OK | rows=10 n_cells=206016 |
| DD (winners) | `avg_min_mtm_winners` | 200 | 10 | 206016 | — | OK | rows=10 n_cells=206016 |
| DD (winners) | `min_mtm_winners` | 200 | 10 | 206016 | — | OK | rows=10 n_cells=206016 |
| DD (overall) | `avg_min_mtm` | 200 | 10 | 206016 | — | OK | rows=10 n_cells=206016 |
| DD (overall) | `min_mtm` | 200 | 10 | 206016 | — | OK | rows=10 n_cells=206016 |
| Frequency | `n_losses` | 200 | 10 | 206016 | — | OK | rows=10 n_cells=206016 |
| Frequency | `n_premium_sl_hit` | 200 | 10 | 206016 | — | OK | rows=10 n_cells=206016 |
| Frequency | `n_rule_trigger` | 200 | 10 | 206016 | — | OK | rows=10 n_cells=206016 |
| Frequency | `n_hard_cap` | 200 | 10 | 206016 | — | OK | rows=10 n_cells=206016 |
| Streaks | `max_consec_losses` | 200 | 10 | 206016 | — | OK | rows=10 n_cells=206016 |
| Streaks | `max_consec_sl_hits` | 200 | 10 | 206016 | — | OK | rows=10 n_cells=206016 |
| Streaks | `max_consec_premium_sl_hits` | 200 | 10 | 206016 | — | OK | rows=10 n_cells=206016 |
| Behavioral | `n_losers_above_avg_max_mtm` | 200 | 10 | 206016 | — | OK | rows=10 n_cells=206016 |
| Behavioral | `avg_loser_exit_offset_minutes` | 200 | 10 | 206016 | — | OK | rows=10 n_cells=206016 |


## Table 4 — Toggle combinations (rule_family × sizing_mode × pick_mode)

| rule_family | sizing_mode | pick_mode | HTTP | rows | n_cells | Verdict |
|---|---|---|---|---|---|---|
| all | capital | by_hour | 200 | 10 | 206016 | OK |
| all | capital | aggregate_hours | 200 | 10 | 34656 | OK |
| all | lots | by_hour | 200 | 10 | 206016 | OK |
| all | lots | aggregate_hours | 200 | 10 | 34656 | OK |
| max_profit | capital | by_hour | 200 | 10 | 64380 | OK |
| max_profit | capital | aggregate_hours | 200 | 10 | 10830 | OK |
| max_profit | lots | by_hour | 200 | 10 | 64380 | OK |
| max_profit | lots | aggregate_hours | 200 | 10 | 10830 | OK |
| margin_target | capital | by_hour | 200 | 10 | 64380 | OK |
| margin_target | capital | aggregate_hours | 200 | 10 | 10830 | OK |
| margin_target | lots | by_hour | 200 | 10 | 64380 | OK |
| margin_target | lots | aggregate_hours | 200 | 10 | 10830 | OK |


## Summary

- Primary:   26 / 26 OK
- Secondary: 22 / 22 OK
- DD-cap:    22 / 22 OK
- Toggles:   12 / 12 OK
