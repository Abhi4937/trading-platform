#!/bin/bash
# Run calibrate_full.py every hour for 24 hours.
# Output appended to scripts/calibration_history.csv (per-run rows with run_ts).
# Per-run logs go to /tmp/calib_loop.log.

set -euo pipefail

PROJECT="/mnt/c/dev/trading_platform/btc_options_fullstack/btc-options"
LOGFILE="/tmp/calib_loop.log"
END_TS=$(($(date +%s) + 24*3600))

cd "$PROJECT"

run=0
while [ $(date +%s) -lt $END_TS ]; do
    run=$((run + 1))
    echo ""                                               | tee -a "$LOGFILE"
    echo "===================================================================="| tee -a "$LOGFILE"
    echo " Run #$run @ $(date -u '+%Y-%m-%d %H:%M:%S UTC')" | tee -a "$LOGFILE"
    echo "====================================================================" | tee -a "$LOGFILE"

    if ! python3 -u scripts/calibrate_full.py >> "$LOGFILE" 2>&1; then
        echo "  WARN: run #$run failed — continuing"     | tee -a "$LOGFILE"
    fi

    # Sleep until next hour mark, or until END_TS — whichever sooner
    NOW=$(date +%s)
    NEXT_RUN=$((NOW + 3600 - (NOW % 3600)))   # top of next hour
    if [ $NEXT_RUN -gt $END_TS ]; then break; fi
    SLEEP_FOR=$((NEXT_RUN - NOW))
    echo "  sleeping ${SLEEP_FOR}s until next hour mark"   | tee -a "$LOGFILE"
    sleep $SLEEP_FOR
done

echo ""                                                    | tee -a "$LOGFILE"
echo "Calibration loop complete. Total runs: $run."        | tee -a "$LOGFILE"
echo "Data: $PROJECT/scripts/calibration_history.csv"     | tee -a "$LOGFILE"
