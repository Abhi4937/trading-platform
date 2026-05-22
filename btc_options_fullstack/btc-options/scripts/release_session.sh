#!/usr/bin/env bash
# release_session.sh — stop a session's backend container and free its slot.
#
# Usage:
#   ./scripts/release_session.sh [SLOT]
#
# If SLOT is omitted, lists active sessions and asks which to release.
# Also kills the matching frontend port if it was started on 3000+SLOT.
set -euo pipefail

LOCK_DIR="$HOME/.btc-options/sessions"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCKER_DIR="$REPO_ROOT/docker"

_list_sessions() {
    echo "Active sessions:"
    local found=0
    for N in 0 1 2 3 4 5 6 7 8 9; do
        local lock="$LOCK_DIR/$((8000 + N)).lock"
        [ -f "$lock" ] || continue
        local container
        container=$(grep -o '"container": *"[^"]*"' "$lock" 2>/dev/null | grep -o '"[^"]*"$' | tr -d '"' || echo "?")
        local running
        running=$(docker inspect --format='{{.State.Running}}' "$container" 2>/dev/null || echo "false")
        printf "  Slot %s  backend :%-4s  frontend :%-4s  container: %s  running: %s\n" \
            "$N" "$((8000+N))" "$((3000+N))" "$container" "$running"
        found=1
    done
    [ "$found" = "0" ] && echo "  (no active sessions)"
}

if [ $# -eq 0 ]; then
    _list_sessions
    echo ""
    printf "Release slot [0-9]: "
    read -r SLOT
else
    SLOT="$1"
fi

BACKEND_PORT=$((8000 + SLOT))
FRONTEND_PORT=$((3000 + SLOT))
LOCK="$LOCK_DIR/${BACKEND_PORT}.lock"

if [ ! -f "$LOCK" ]; then
    echo "No lock file for slot $SLOT (port $BACKEND_PORT). Nothing to release."
    # Still try to stop the container if it exists
fi

CONTAINER="docker-backend-session-${SLOT}-1"

echo ""
echo "=== Releasing slot $SLOT ==="

# Stop and remove the backend container
echo "  Stopping $CONTAINER..."
docker compose \
    -p "btc_session_${SLOT}" \
    -f "$DOCKER_DIR/docker-compose.session.yml" \
    down 2>&1 | grep -v "^time=" || \
docker stop "$CONTAINER" 2>/dev/null || true

# Kill frontend — try stored PID first, fall back to port-based kill
FRONTEND_PID=$(grep -o '"frontend_pid": *[0-9]*' "$LOCK" 2>/dev/null | grep -o '[0-9]*$' || echo "")
if [ -n "$FRONTEND_PID" ] && kill -0 "$FRONTEND_PID" 2>/dev/null; then
    kill "$FRONTEND_PID" 2>/dev/null && echo "  Killed frontend PID $FRONTEND_PID"
fi
if command -v fuser >/dev/null 2>&1; then
    fuser -k "${FRONTEND_PORT}/tcp" 2>/dev/null && \
        echo "  Killed any remaining process on port $FRONTEND_PORT" || true
fi

# Remove lock file
rm -f "$LOCK"
echo "  Lock file removed."
echo ""
echo "Slot $SLOT is free. Run claim_session.sh to claim a new slot."
