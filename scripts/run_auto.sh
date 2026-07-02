#!/usr/bin/env bash
# Cron wrapper for `oa auto`. Serializes runs (flock), activates the venv,
# and appends stdout/stderr to output/auto_cron.log. Install with e.g.:
#
#   crontab -e
#   0 7 * * 1-5  /home/rtasseff/projects/OAData/scripts/run_auto.sh
#
# (WSL note: cron only fires while WSL is running. Either enable systemd
# + cron in WSL, or drive this from Windows Task Scheduler with:
#   wsl.exe -d <distro> -- /home/rtasseff/projects/OAData/scripts/run_auto.sh )
set -u
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCK_FILE="$PROJECT_DIR/output/.auto.lock"
LOG_FILE="$PROJECT_DIR/output/auto_cron.log"

mkdir -p "$PROJECT_DIR/output"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "$(date -Is)  skipped: previous run still in progress" >>"$LOG_FILE"
    exit 0
fi

cd "$PROJECT_DIR"
{
    echo "=== $(date -Is) oa auto ==="
    "$PROJECT_DIR/.venv/bin/python" -m oa_tracker auto
    echo "=== exit $? ==="
} >>"$LOG_FILE" 2>&1
