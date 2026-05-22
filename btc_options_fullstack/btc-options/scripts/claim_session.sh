#!/usr/bin/env bash
# claim_session.sh — claim a free backend slot and bring up its container.
#
# Usage:
#   ./scripts/claim_session.sh [--build]
#
# Picks the first free slot in 0-9, brings up docker-backend-session-N-1
# on port 8000+N, writes a lock file, then prints instructions for starting
# the matching frontend. Slot 0 is primary (live ticker enabled); rest are
# secondary (DISABLE_LIVE_TICKER=1).
#
# Options:
#   --build   Force docker image rebuild (needed after pip/Dockerfile changes)
#
# Shared resources — safe from any slot:
#   ~/btc-data/derived/  parquet caches, strike-index.json — atomic writes, read-shared
#   Redis DB /N          each slot has its own DB, no key collisions
#
# What NOT to do from a secondary session (slot 1-9):
#   - docker compose up --build -d backend   (rebuilds docker-backend-1, kills slot 0)
#   - fuser -k 3000/tcp                      (kills slot 0 frontend)
#   - Assume localhost:8000 — use YOUR port printed below
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCKER_DIR="$REPO_ROOT/docker"
LOCK_DIR="$HOME/.btc-options/sessions"
MUTEX="$LOCK_DIR/.mutex"
BUILD_FLAG=""

for arg in "$@"; do
    [ "$arg" = "--build" ] && BUILD_FLAG="--build"
done

mkdir -p "$LOCK_DIR"

# ─── Mutex: atomic scan-and-claim via mkdir (atomic on NTFS + ext4) ──────────
_acquire_mutex() {
    local tries=0
    while ! mkdir "$MUTEX" 2>/dev/null; do
        tries=$((tries + 1))
        [ $tries -gt 50 ] && { echo "ERROR: could not acquire session lock after 5s"; exit 1; }
        sleep 0.1
    done
}
_release_mutex() { rmdir "$MUTEX" 2>/dev/null || true; }
trap _release_mutex EXIT

# ─── Stale-lock check ─────────────────────────────────────────────────────────
_slot_is_free() {
    local n="$1"
    local lock="$LOCK_DIR/$((8000 + n)).lock"
    [ ! -f "$lock" ] && return 0

    # Parse container name from lock file (simple grep, no jq dependency)
    local container
    container=$(grep -o '"container": *"[^"]*"' "$lock" 2>/dev/null | grep -o '"[^"]*"$' | tr -d '"' || echo "")
    [ -z "$container" ] && { rm -f "$lock"; return 0; }

    local running
    running=$(docker inspect --format='{{.State.Running}}' "$container" 2>/dev/null || echo "false")
    [ "$running" != "true" ] && { rm -f "$lock"; return 0; }

    return 1  # slot is live
}

# ─── Claim first free slot ────────────────────────────────────────────────────
_acquire_mutex

SLOT=""
for N in 0 1 2 3 4 5 6 7 8 9; do
    if _slot_is_free "$N"; then
        SLOT="$N"
        break
    fi
done

if [ -z "$SLOT" ]; then
    _release_mutex
    echo "ERROR: all slots 0-9 are taken. Run 'release_session.sh' to free one."
    docker ps --filter "name=docker-backend-session" --format "  {{.Names}}  {{.Status}}  {{.Ports}}"
    exit 1
fi

BACKEND_PORT=$((8000 + SLOT))
FRONTEND_PORT=$((3000 + SLOT))
REDIS_DB=$SLOT
CONTAINER="docker-backend-session-${SLOT}-1"
[ "$SLOT" = "0" ] && DISABLE_TICKER=0 || DISABLE_TICKER=1
[ "$SLOT" = "0" ] && IS_PRIMARY="yes (live ticker ON)" || IS_PRIMARY="no (DISABLE_LIVE_TICKER=1)"

# Write lock before releasing mutex
cat > "$LOCK_DIR/${BACKEND_PORT}.lock" <<EOF
{
  "slot": $SLOT,
  "backend_port": $BACKEND_PORT,
  "frontend_port": $FRONTEND_PORT,
  "container": "$CONTAINER",
  "pid": $$,
  "claimed_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "primary": $([ "$SLOT" = "0" ] && echo true || echo false)
}
EOF

_release_mutex

# ─── Start backend container ──────────────────────────────────────────────────
echo ""
echo "=== Claiming slot $SLOT — backend :$BACKEND_PORT — frontend :$FRONTEND_PORT ==="
echo "    primary: $IS_PRIMARY"
echo ""

export SESSION_SLOT="$SLOT"
export BACKEND_PORT="$BACKEND_PORT"
export REDIS_DB="$REDIS_DB"
export DISABLE_LIVE_TICKER="$DISABLE_TICKER"

docker compose \
    -p "btc_session_${SLOT}" \
    -f "$DOCKER_DIR/docker-compose.yml" \
    -f "$DOCKER_DIR/docker-compose.session.yml" \
    up $BUILD_FLAG -d backend_session 2>&1 | grep -v "^time=" || true

echo ""
echo "=== Backend starting — waiting for ready... ==="
READY=0
for i in $(seq 1 60); do
    sleep 2
    STATUS=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:${BACKEND_PORT}/health" 2>/dev/null || echo "000")
    if [ "$STATUS" = "200" ]; then
        READY=1
        break
    fi
    printf "  [%ds] waiting (status=%s)...\n" "$((i*2))" "$STATUS"
done

if [ "$READY" = "0" ]; then
    echo "WARNING: backend did not respond in 120s — check: docker logs $CONTAINER"
fi

# ─── Print instructions ────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
printf "║  Slot %-2s ready                                              ║\n" "$SLOT"
printf "║  Backend  : http://localhost:%-5s                           ║\n" "$BACKEND_PORT"
printf "║  Frontend : http://localhost:%-5s                           ║\n" "$FRONTEND_PORT"
printf "║  Primary  : %-49s║\n" "$IS_PRIMARY"
printf "║  Container: %-49s║\n" "$CONTAINER"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Start frontend (in a new terminal):                         ║"
echo "║                                                              ║"
printf "║  cd frontend                                                 ║\n"
printf "║  VITE_API_URL=http://localhost:%s/api/v1 \\\\                ║\n" "$BACKEND_PORT"
printf "║  VITE_API_BASE=http://localhost:%s \\\\                      ║\n" "$BACKEND_PORT"
printf "║  VITE_WS_HOST=localhost:%s \\\\                              ║\n" "$BACKEND_PORT"
printf "║  npm run dev -- --port %s                                   ║\n" "$FRONTEND_PORT"
echo "║                                                              ║"
echo "║  On session end: ./scripts/release_session.sh $SLOT            ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "Logs: docker logs -f $CONTAINER"
