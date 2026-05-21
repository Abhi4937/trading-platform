# Claude Instructions — BTC Options Platform

## RULE #1 — Always Ask Before Making Changes
Before editing ANY file (code, config, docs, anything):
1. Describe what you plan to change and why
2. Ask "shall I proceed?" and wait for confirmation
Do NOT assume a question about behavior is a request to fix it.

## RULE #2 — Commit Before Starting New Work
Before making any new change, check `git status`.
If there are uncommitted changes, ask the user: "There are uncommitted changes — shall I commit and push first?"
Do NOT start new work on top of uncommitted changes.

## RULE #3 — Margin Model Safety Bias (HARD INVARIANT — added 2026-05-01)
The margin engines (`scripts/margin_engine.py` + `frontend/src/utils/marginEngine.ts`)
**must always over-estimate, never under-estimate** Delta's actual ARM (the "Order
Margin" shown in UI when placing an order — that's the `additional_required_margin`
field of `/v2/orders/estimate_margin/basket`, NOT the `portfolio_margin` field which
is gross and does not match what's actually charged).

Currently enforced via `SAFETY_BUFFER_PCT = 0.20` constant applied as final multiplier
on `portfolio_margin`. Both engines must keep this invariant in sync. When refitting
constants, bias the loss to keep residuals on the over-charge side. Verify any change
against fresh UI numbers at multiple lot sizes before committing.

## RULE #4 — Coordinate with Other Claude Sessions
Multiple Claude sessions may be running against this repo at the same time.
Before doing anything that touches shared state, check for conflicts and wait
if another session is mid-flight. Specifically:

1. **Before restarting backend** (`docker compose up --build -d backend`) or
   **frontend** (`fuser -k 3000/tcp && npm run dev`), or
   **before driving the UI via Playwright MCP**, run:
   ```
   ps -ef | grep -E "claude|fuser|npm run dev|docker compose|vite" | grep -v grep
   ```
   If you see another shell mid-restart (e.g. another `fuser -k 3000/tcp`,
   `npm run dev` starting up, or a `docker compose up --build` running),
   **wait for it to finish** rather than racing it. Killing port 3000 while
   the other session is starting Vite will leave the user with a broken
   frontend.
2. **Before editing files Gemini may also be editing**, re-read `git status`
   and `HANDOFF.md` — both AI assistants share this repo.
3. **If unsure, ask the user**: "I see another session is currently
   restarting the frontend / accessing the UI — should I wait, or proceed?"

## RULE #5 — Long-running Scripts MUST Use a Dedicated Container (added 2026-05-13)

Backend container `docker-backend-1` is shared with the API. It can be
restarted at any time — by another Claude session, by my own
`docker compose up --build` for code reloads, by user intervention, or by
external triggers. **Any long-running script started via `docker exec` (with
or without `-d`) dies with the container restart, losing all progress.**

This already cost us 2 partial backtests in Session 24/25.

### The pattern — use `docker compose run` for one-shot scripts

For any backtest, grid build, calibration loop, or other script that runs
>5 minutes:

```bash
# WRONG — dies on container restart:
docker exec -d docker-backend-1 sh -c "python -m app.analytics.foo > /tmp/foo.log 2>&1"

# RIGHT — separate container with its own lifecycle, survives backend restarts:
docker compose run -d --rm \
  --name foo_$(date +%s) \
  backend python -m app.analytics.foo
```

`docker compose run`: new container based on the `backend` service definition,
same image/env/volumes/network, `--rm` removes on exit, `-d` detaches, has its
OWN lifecycle independent of `docker-backend-1`. Logs via `docker logs <name>`.

### Verify before claiming "long script is running"

After launching with `docker compose run -d`, confirm the dedicated
container exists with a different ID than `docker-backend-1`:

```bash
docker ps --filter "name=foo_" --format "{{.Names}} {{.Status}}"
docker logs --tail 20 foo_<timestamp>
```

If the script is critical and might run for hours, also write logs to
the shared bind-mount so they survive everything:

```bash
docker compose run -d --rm --name foo_$(date +%s) \
  -v /home/abhis/btc-data/logs:/logs \
  backend sh -c "python -m app.analytics.foo > /logs/foo_$(date +%s).log 2>&1"
```

Precedent: M7 v6 grid build (Session 23) ran 4h 20m through multiple
`docker-backend-1` restarts without issue using this pattern.

## RULE #6 — Per-session Backend Isolation (added 2026-05-20)

When two Claude sessions are doing active backend development in parallel,
each session should run its own isolated backend container so a rebuild in
one session never clobbers the other's in-memory state (backtest jobs,
caches, ticker stream).

### Session A (primary — unchanged)
Session A uses the canonical `docker-backend-1` on port **8000** and the
frontend on port **3000**, exactly as always.

### Session B (secondary — isolated)
Session B runs its own container (`docker-backend-session-b-1`) on port
**8001** using `docker/docker-compose.session-b.yml`.

**Start Session B backend** (from the `docker/` directory):
```bash
docker compose -f docker-compose.yml -f docker-compose.session-b.yml up --build -d backend_session_b
```

**Start Session B frontend** (PowerShell, from repo root):
```powershell
$env:VITE_API_URL="http://localhost:8001/api/v1"
$env:VITE_API_BASE="http://localhost:8001"
$env:VITE_WS_HOST="localhost:8001"
cd frontend; npm run dev -- --port 3001
```

**Stop Session B backend**:
```bash
docker compose -f docker-compose.yml -f docker-compose.session-b.yml stop backend_session_b
```

**Stop Session B frontend**: `fuser -k 3001/tcp`

### Key properties
- **Redis DB `/1`**: Session B uses Redis DB 1 instead of DB 0 — no key collisions.
- **Live ticker disabled**: `DISABLE_LIVE_TICKER=1` skips the Delta WS subscription and live
  recorder in Session B. Do NOT use Session B for live-ticker feature development; use Session A.
- **Shared disk caches**: bind-mounts at `~/btc-data/derived/...` are shared — both sessions
  read the same parquet caches. If B writes a new derived parquet, A can see it immediately.
- **Merge-time**: once Session B's code is committed and merged, rebuild `docker-backend-1`
  normally. The canonical `:3000` frontend picks up all changes automatically — no extra wiring.

### Verify isolation
```bash
# Both containers should show different IDs:
docker ps | grep backend
# Different session UUIDs = independent processes:
curl http://localhost:8000/api/v1/session-id
curl http://localhost:8001/api/v1/session-id
```

## Session Start Checklist (do this first, every session)
1. Read `HANDOFF.md` — who worked last, what changed, what's pending
2. Read `docs/memories/current_state.md` — active tasks and open issues
3. Read `docs/memories/work_log_gemini.md` — what Gemini did (avoid conflicts)
4. Then ask the user what they want to work on

## Handoff Protocol (do this at end of every session)
1. Update `HANDOFF.md` — fill in: who worked, what files changed, what's pending
2. Update `docs/memories/work_log_claude.md` — append what was done
3. Update `docs/memories/current_state.md` — if anything changed
4. Tell the user: "Ready to hand off to Gemini — HANDOFF.md is updated"

## Auto-handoff at 90% / 95% / 98% session-context usage (added 2026-05-07)
The status line in this repo shows live session token usage (script:
`~/.claude/statusline-command.sh`, denominator = 1,000,000 tokens / Opus 4.7
1M context). Watch the percentage and proactively run a partial handoff each
time it crosses one of these thresholds — without waiting for the user to ask:

- **At 90%** — Update `HANDOFF.md`, `docs/memories/work_log_claude.md`, and
  `docs/memories/current_state.md` with a *checkpoint* of what's been done
  in this session so far. Tell the user: "Session at 90% — checkpoint
  written. Safe to keep going for now." Also update relevant entries in
  `~/.claude/projects/.../memory/` if any new project/feedback memories
  emerged.
- **At 95%** — Refresh the same files with the latest state. Tell the user:
  "Session at 95% — handoff files refreshed. Approaching the limit — wrap
  up the in-flight task or restart soon."
- **At 98%** — Final refresh, then STOP starting new work. Tell the user:
  "Session at 98% — final handoff written. Please /clear or /compact and
  restart. Don't ask me to do more in this session."

Do not wait for the user's permission to write these checkpoints — RULE #1
("ask before changes") is overridden for this specific protocol because the
whole point is to capture state before context runs out. Each checkpoint
should be incremental: append new items to `work_log_claude.md`; rewrite
the "current task" block in `HANDOFF.md`; only mutate `current_state.md`
if something there changed. Don't churn the files.

## AI Collaboration with Gemini
- Claude and Gemini take turns on this codebase
- Gemini reads the same `HANDOFF.md` and `docs/memories/` files
- Never overwrite a file Gemini touched without reading it first
- Check `git status` before starting work to see Gemini's uncommitted changes

## Endpoints
- Delta REST: `https://api.india.delta.exchange`
- Delta WebSocket: `wss://socket.india.delta.exchange`
- Always use India region endpoints — matches user's trading account

## Architecture Rules
- Backend runs with `--workers 1` (single uvicorn worker) — required for in-memory ticker_store
- Never increase workers without switching to Redis-backed ticker store
- Frontend is Vite dev server on port 3000 (not containerised)

## Branch Strategy
- `main` — full featured (IV chart, Premium chart)
- `feature/chain-only-no-charts` — clean chain-only for testing/new development

## After Any Code Change

### Frontend change
1. Kill and restart frontend: `fuser -k 3000/tcp && cd frontend && npm run dev`
2. **Verify the changed UI with Playwright MCP** before declaring the change done:
   navigate to `http://localhost:3000`, exercise the affected feature, take a
   screenshot, and confirm the visible state matches what you intended. Don't
   just rely on type-checks or curl — type-checks verify code correctness, not
   feature correctness. If Playwright MCP tools aren't yet loaded in the
   current Claude Code session (newly-installed MCP servers require a session
   restart), tell the user and pause for the restart.

### Backend change
1. Rebuild and restart backend: `cd docker && docker compose up --build -d backend`
2. Then kill and restart frontend: `fuser -k 3000/tcp && cd frontend && npm run dev`
3. **Verify the affected UI surface with Playwright MCP** as above — backend
   changes that flow into the dashboard must be exercised through the browser,
   not just via `curl`, to catch wiring/serialization mismatches.

- Always restart/rebuild immediately after making changes — do not wait for user to ask
- Once the user confirms the change works, commit and push to current branch immediately

## Shell Environment

Claude Code runs in **Git Bash on Windows** (not WSL, not PowerShell).
Project root: `C:/dev/trading_platform/btc_options_fullstack/btc-options`

### Available tools
`cat` `head` `tail` `grep` `rg` `jq` `awk` `sed` `find` `ls` `wc` `diff`
`git` `docker` `docker compose` `node` `npm` `npx` `python3` `pip` `duckdb` `curl` `wget`

### Missing tools (do NOT invoke)
`tree` `fd` `yq` `bat` — not installed; use `find`/`ls`/`jq`/`cat` equivalents.

### Avoid PowerShell subexpressions
PowerShell `$(...)` subexpressions in Bash tool calls (e.g. `$(Get-Date)`,
`$(New-Item ...)`) trigger a separate approval prompt every single time.
Use POSIX-shell equivalents instead:
- date → `date +%s` (not `$(Get-Date -UFormat %s)`)
- temp path → `/tmp/foo_$$` (not `$env:TEMP`)
- variable expansion → `"$VAR"` (not `"$($env:VAR)"`)

When you need to set a Windows env-var for a one-off command, prefix with the
Bash `export` or inline `VAR=value command` syntax — not PowerShell `$env:`.

## Workflow Rules

### Model routing (opusplan)
- **Plan mode** (`/plan`, `/opusplan`) → runs on **Opus** (planning + verification).
- **Execute mode** (normal session, after plan approval) → runs on **Sonnet** (implement + tests).
- **Error diagnosis / traceback analysis / root-cause investigation / issue solving** → always **Opus**. Do not delegate to subagents or switch to a lighter model for these tasks.
- The model is selected by the harness automatically; do not try to switch mid-task.

### What counts as a non-trivial change (requires /plan first)
Any of the following triggers the plan → implement → verify cycle:
- New API endpoint or changes to existing endpoint contracts
- Changes to backtest engine logic, margin engine, or slippage model
- New database schema / parquet column layout changes
- Adding or removing a UI component that crosses page boundaries
- Multi-file refactors touching ≥ 3 files
- Any change to `CLAUDE.md`, `.claude/settings.json`, `docker-compose*.yml`

Single-file typo fixes, comment changes, and one-liner constant tweaks are
trivial — proceed directly without /plan.

### Mandatory workflow for non-trivial changes
```
PLAN  →  IMPLEMENT  →  VERIFY (TRACE, DO NOT REVIEW)  →  RUN TESTS  →  ESCALATE
```
1. **PLAN** — write plan to plan file, get user approval via ExitPlanMode.
2. **IMPLEMENT** — make the code changes. One logical commit per logical unit.
3. **VERIFY** — use `Bash(grep ...)`, `Bash(rg ...)`, or Playwright MCP to
   **trace the actual execution path** through the changed code. DO NOT just
   re-read the diff and say "looks correct". Trace the data flow end-to-end.
   For numerical code, mentally execute with real inputs (real BTC option
   symbol, ATM strike, wing strike, expiry at boundaries, OI=0, mark=0).
4. **RUN TESTS** — `docker exec docker-backend-1 python -m pytest ...` for
   backend; `npx tsc --noEmit` for frontend type-checks.
5. **ESCALATE** — if VERIFY reveals a mismatch between intent and
   implementation, fix it before reporting done.

### Trivial-change exemptions
The following skip /plan but still require VERIFY + commit:
- Constant / threshold value changes (e.g. `SAFETY_BUFFER_PCT`)
- Renaming a variable within a single function
- Adding/updating a comment or docstring
- Fixing a typo in a UI label

### Anti-patterns (never do these)
- Declare "done" after writing code without tracing execution.
- Run `grep` on the diff and call that "verified". Read the runtime path.
- Skip the commit step after a working change — uncommitted work blocks the next task.
- Start new work when `git status` shows modified files (RULE #2).
- Restart `docker-backend-1` during a long-running script (RULE #5).
- Recommend Haiku for this project — domain is too demanding.

## Domain Reminders
- **Expiry time**: Delta Exchange India daily options expire at **5:30 PM IST = 12:00 UTC**.
- **Symbol format**: `C-BTC-{strike}-{DDMMYY}` (e.g. `C-BTC-95000-200526`).
- **Strangle parameters**: ~0.10 delta both legs, stop loss = 2× entry premium.
- **IV/RV filter**: only enter when IV/RV > 1.3 (implied vol premium over realized).
- **Strike-union freeze bug**: use `>= entry_time` not `> entry_time` when building
  the strike union set in the backtest day-loop to avoid missing the entry bar.
- **btc-collector data**: ~40–50 GB Parquet at `C:/Users/Abhis/btc-collector/`.
  Never touch, move, or delete without explicit user approval.
- **IP rate limit**: Delta Exchange rate-limits by IP. WSL IP rotates on each
  Docker restart — if `delta_arm` column is empty in calibration CSV, the
  IP whitelist on the Delta dashboard needs updating (user action required).

## Reference Documents
For details not in this file:

- **Architecture & features**: `docs/ARCHITECTURE.md` — Historical Dashboard,
  Backtest Dashboard, strike criteria, trade log layout, indicators, OI display
- **Calibration & margin**: `docs/CALIBRATION.md` — calibration loop mechanics,
  slippage model constants, key Greek/spot facts
- **Current state**: `HANDOFF.md` + `docs/memories/current_state.md`
