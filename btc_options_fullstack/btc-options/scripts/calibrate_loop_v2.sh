#!/bin/bash
# Run scripts/calibrate_v2.py every 15 minutes for 24 hours (96 runs).
# Output appended to scripts/calibration_v2_history.csv (per-run rows with run_ts).
# Per-run logs go to /tmp/calib_v2_loop.log.

set -uo pipefail

PROJECT="/mnt/c/Users/Abhis/OneDrive/Desktop/Trading/Trading platform/btc_options_fullstack/btc-options"
LOGFILE="/tmp/calib_v2_loop.log"
INTERVAL=900           # 15 minutes
DURATION=$((24*3600))  # 24 hours
END_TS=$(($(date +%s) + DURATION))

cd "$PROJECT"

run=0
while [ $(date +%s) -lt $END_TS ]; do
    run=$((run + 1))
    echo ""                                                     | tee -a "$LOGFILE"
    echo "====================================================" | tee -a "$LOGFILE"
    echo " v2 Run #$run @ $(TZ='Asia/Kolkata' date '+%Y-%m-%d %H:%M:%S IST')" | tee -a "$LOGFILE"
    echo "====================================================" | tee -a "$LOGFILE"

    if ! python3 -u scripts/calibrate_v2.py >> "$LOGFILE" 2>&1; then
        echo "  WARN: run #$run failed — continuing"             | tee -a "$LOGFILE"
    fi

    NOW=$(date +%s)
    REMAINING=$((END_TS - NOW))
    if [ $REMAINING -le 0 ]; then break; fi
    SLEEP_FOR=$INTERVAL
    if [ $SLEEP_FOR -gt $REMAINING ]; then SLEEP_FOR=$REMAINING; fi
    echo "  sleeping ${SLEEP_FOR}s until next run"              | tee -a "$LOGFILE"
    sleep $SLEEP_FOR
done

echo ""                                                          | tee -a "$LOGFILE"
echo "v2 loop complete. Total runs: $run."                       | tee -a "$LOGFILE"
echo "Data: $PROJECT/scripts/calibration_v2_history.csv"        | tee -a "$LOGFILE"
