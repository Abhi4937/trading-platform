#!/usr/bin/env bash
# Starts the full BTC options trading platform stack with one command.
#
# Brings up:
#   1. Backend (docker compose) — FastAPI + recorder + merge scheduler
#   2. Historical collector (btc-collector) — REST batch fetcher, resume mode
#   3. Frontend (Vite dev server, port 3000)
#
# Stops everything cleanly on Ctrl+C.
#
# Usage:
#   ./scripts/start-platform.sh                 # start everything
#   ./scripts/start-platform.sh --no-collector  # skip the historical collector
#   ./scripts/start-platform.sh --no-frontend   # skip Vite (e.g. running it elsewhere)
#   ./scripts/start-platform.sh --status        # show what's running, exit
#   ./scripts/start-platform.sh --stop          # stop everything, exit
#
# Logs:
#   /tmp/btc_collector.log      — collector stdout
#   /tmp/frontend_dev.log       — Vite stdout
#   ~/btc-data/logs/            — collector's own structured logs
#   ~/btc-data/logs/live_recorder.log — live WS recorder (when running)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COLLECTOR_DIR="/mnt/c/Users/Abhis/btc-collector"

NO_COLLECTOR=0
NO_FRONTEND=0
SHOW_STATUS=0
DO_STOP=0

for arg in "$@"; do
  case "$arg" in
    --no-collector) NO_COLLECTOR=1 ;;
    --no-frontend)  NO_FRONTEND=1 ;;
    --status)       SHOW_STATUS=1 ;;
    --stop)         DO_STOP=1 ;;
    -h|--help)
      sed -n '2,18p' "$0"; exit 0 ;;
    *)
      echo "Unknown arg: $arg"; exit 1 ;;
  esac
done

c_grn() { printf '\033[32m%s\033[0m' "$1"; }
c_red() { printf '\033[31m%s\033[0m' "$1"; }
c_dim() { printf '\033[2m%s\033[0m' "$1"; }

is_backend_up() {
  curl -fsS http://localhost:8000/health > /dev/null 2>&1
}
is_collector_up() {
  pgrep -f "btc-collector.*main\.py" > /dev/null 2>&1
}
is_frontend_up() {
  curl -fsS http://localhost:3000 > /dev/null 2>&1 \
    || ss -ltn 2>/dev/null | grep -q ":3000\b"
}

show_status() {
  echo "platform status:"
  if is_backend_up;   then echo "  backend:    $(c_grn UP)   http://localhost:8000"
                     else echo "  backend:    $(c_red DOWN)"; fi
  if is_collector_up; then echo "  collector:  $(c_grn UP)   PID=$(pgrep -f 'btc-collector.*main\.py' | head -1)"
                     else echo "  collector:  $(c_red DOWN)"; fi
  if is_frontend_up;  then echo "  frontend:   $(c_grn UP)   http://localhost:3000"
                     else echo "  frontend:   $(c_red DOWN)"; fi
}

stop_all() {
  echo "stopping platform..."
  if is_collector_up; then
    pkill -f "btc-collector.*main\.py" || true
    echo "  collector: stopped"
  fi
  if is_frontend_up; then
    fuser -k 3000/tcp 2>/dev/null || true
    echo "  frontend: stopped"
  fi
  if is_backend_up; then
    (cd "$REPO_ROOT/docker" && docker compose down) || true
    echo "  backend: stopped"
  fi
}

start_backend() {
  if is_backend_up; then
    echo "  backend: already up — skipping"
    return
  fi
  echo "  backend: starting via docker compose..."
  (cd "$REPO_ROOT/docker" && docker compose up -d backend redis)
  for i in {1..30}; do
    if is_backend_up; then echo "  backend: $(c_grn ready)"; return; fi
    sleep 1
  done
  echo "  backend: $(c_red 'failed to come up in 30s')"; return 1
}

start_collector() {
  [ "$NO_COLLECTOR" = "1" ] && { echo "  collector: skipped (--no-collector)"; return; }
  if is_collector_up; then
    echo "  collector: already up — skipping"
    return
  fi
  if [ ! -d "$COLLECTOR_DIR" ]; then
    echo "  collector: $(c_red "not found at $COLLECTOR_DIR — skipping")"
    return
  fi
  echo "  collector: starting (resume mode)..."
  (cd "$COLLECTOR_DIR" && nohup python3 main.py resume > /tmp/btc_collector.log 2>&1 &)
  sleep 2
  if is_collector_up; then echo "  collector: $(c_grn started)"
  else echo "  collector: $(c_red 'failed to start — check /tmp/btc_collector.log')"; fi
}

start_frontend() {
  [ "$NO_FRONTEND" = "1" ] && { echo "  frontend: skipped (--no-frontend)"; return; }
  if is_frontend_up; then
    echo "  frontend: already up — skipping"
    return
  fi
  echo "  frontend: starting Vite dev server..."
  (cd "$REPO_ROOT/frontend" && nohup npm run dev > /tmp/frontend_dev.log 2>&1 &)
  for i in {1..20}; do
    if is_frontend_up; then echo "  frontend: $(c_grn ready)"; return; fi
    sleep 1
  done
  echo "  frontend: $(c_red 'failed to come up in 20s — check /tmp/frontend_dev.log')"
}

# ── Dispatch ──────────────────────────────────────────────────────────────────

if [ "$SHOW_STATUS" = "1" ]; then show_status; exit 0; fi
if [ "$DO_STOP"     = "1" ]; then stop_all;    exit 0; fi

echo "starting platform..."
start_backend
start_collector
start_frontend
echo
show_status
echo
echo "$(c_dim 'live logs:')"
echo "  $(c_dim 'collector:'    ) tail -f /tmp/btc_collector.log"
echo "  $(c_dim 'frontend:'     ) tail -f /tmp/frontend_dev.log"
echo "  $(c_dim 'backend:'      ) docker logs -f docker-backend-1"
echo "  $(c_dim 'live recorder:') docker exec docker-backend-1 tail -f /home/abhis/btc-data/logs/live_recorder.log"
echo
echo "$(c_dim 'stop with:') ./scripts/start-platform.sh --stop"
