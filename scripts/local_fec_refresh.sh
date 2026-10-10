#!/bin/bash
# Weekly FEC refresh, run locally by launchd (see scripts/com.andykim.congress-fec-refresh.plist).
# Runs here rather than in GitHub Actions because retention and industry
# signals are computed from the gitignored raw fingerprint cache in
# .cache/fec_raw.db, which a CI runner never has.
#
# Ingests new contributions (current cycle is reopened when stale), recomputes
# retention + industry signals, then publishes ONLY the FEC tables on top of
# the latest origin/main so CI's bills data isn't overwritten.

set -uo pipefail
cd "$(dirname "$0")/.."

LOG="$HOME/Library/Logs/congress-fec-refresh.log"
mkdir -p "$(dirname "$LOG")"
exec >>"$LOG" 2>&1

notify_failure() {
  osascript -e 'display notification "Check ~/Library/Logs/congress-fec-refresh.log" with title "Congress+FEC refresh failed"' || true
}

echo "=== $(date '+%Y-%m-%d %H:%M:%S %Z') start ==="
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
PY="venv/bin/python"

# A sleeping laptop wakes straight into this job; wait for the network.
for _ in 1 2 3 4 5 6; do
  curl -fsS -o /dev/null --max-time 10 https://api.open.fec.gov/ && break
  sleep 20
done

if ! "$PY" scripts/refresh_fec.py --chunk-size 400; then
  echo "refresh_fec.py failed"; notify_failure; exit 1
fi

if ! "$PY" scripts/publish_db.py --owner fec --message "chore: refresh FEC data (local)"; then
  echo "publish_db.py failed"; notify_failure; exit 1
fi

echo "=== $(date '+%Y-%m-%d %H:%M:%S %Z') done ==="
