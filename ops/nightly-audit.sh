#!/usr/bin/env bash
#
# Nightly drift check: re-run the nightly-assurance battery against the saved
# qwen3-8b-nightly baseline and leave a machine-readable status behind.
#
# The comparison itself is not implemented here on purpose. `cli.py monitor`
# already re-runs a suite, compares it to a baseline with the project's
# uncertainty rules (D-004, D-011), writes the status JSON, and exits non-zero
# on drift. A second opinion about what counts as drift, living in bash, would
# be a second set of thresholds to keep in sync and a second thing to audit.
# This script's whole job is to get that command to run unattended and to
# report faithfully what it said.
#
# Usage:
#   ops/nightly-audit.sh              run the battery (what systemd invokes)
#   ops/nightly-audit.sh --dry-run    do everything except call the model
#   ops/nightly-audit.sh --help
#
# Exit codes are the CLI's own (0 clean, 4 drift, see cli.py), with one
# addition: 75 when the endpoint never came up. See the exit-code note below.

set -euo pipefail

# systemd user services inherit almost nothing, and cron even less, so nothing
# below may assume a PATH or a shell profile. Binaries are named absolutely and
# PATH is set explicitly for the child processes `uv run` spawns.
export PATH="/home/justin-a/.local/bin:/usr/local/bin:/usr/bin:/bin"
UV_BIN="${UV_BIN:-/home/justin-a/.local/bin/uv}"
CURL_BIN="${CURL_BIN:-/usr/bin/curl}"

# Derived rather than hardcoded: the systemd unit already pins one absolute path
# to this file, and a second copy of the repo location would be a second thing
# to fix the day the repo moves.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname -- "$SCRIPT_DIR")"

# Contract paths. XDG_STATE_HOME is honoured rather than assumed so the script
# still lands in the right place under a session that sets it; the fallback is
# the documented location.
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/ai-audit"
STATUS_OUT="$STATE_DIR/monitor-status.json"
LOG_DIR="$STATE_DIR/logs"

SUITE="suites/nightly.json"
BASELINE_LABEL="qwen3-8b-nightly"
ADAPTER="openai"
MODEL="qwen3:8b"

# The local ollama server speaks the OpenAI wire format, so the openai adapter
# points at it. The key is a placeholder: adapters/remote.py refuses to build a
# real adapter without one (D-001, no silent downgrade to the mock), and ollama
# ignores its value.
export OPENAI_API_KEY="ollama"
export OPENAI_BASE_URL="http://localhost:11434/v1"

# Overridable so the wait loop can be exercised against a URL that is known to
# be down, without waiting for a real outage to find out whether it bounds.
OLLAMA_PROBE_URL="${OLLAMA_PROBE_URL:-http://localhost:11434/api/tags}"
# This desktop is suspended overnight, so a catch-up run starts seconds after
# the lid opens, while ollama's own unit is still starting and the GPU driver
# is still settling. Five minutes of patience turns "endpoint refused the
# connection" from a nightly false alarm into a rare, real one.
WAIT_TIMEOUT_SECS="${WAIT_TIMEOUT_SECS:-300}"
WAIT_INTERVAL_SECS="${WAIT_INTERVAL_SECS:-10}"
PROBE_TIMEOUT_SECS="${PROBE_TIMEOUT_SECS:-5}"

# Roughly a month of nightly runs: long enough to see a slow trend when
# something looks off, short enough that nobody has to think about it.
LOG_KEEP="${LOG_KEEP:-30}"

# 75 is sysexits' EX_TEMPFAIL, chosen because it cannot collide with the CLI's
# own 0-4. "The endpoint was down" and "the model drifted" are different
# incidents and must not arrive looking alike.
EXIT_ENDPOINT_UNAVAILABLE=75

DRY_RUN=0

usage() {
    sed -n '3,21p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'nightly-audit: unknown argument %q\n' "$1" >&2; exit 2 ;;
    esac
    shift
done

mkdir -p -- "$LOG_DIR" "$STATE_DIR"

RUN_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_FILE="$LOG_DIR/nightly-$RUN_STAMP.log"

# Everything from here goes to both the per-run log and stdout, which systemd
# captures into the journal. The owner reads whichever is closer to hand:
# `journalctl --user -u ai-audit-nightly` right after a failure, or the log file
# weeks later when the journal has rotated away.
exec > >(tee -a -- "$LOG_FILE") 2>&1

log() {
    printf '%s  %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

# Pruning runs even when the battery failed, so a stretch of broken nights
# cannot quietly fill the state directory.
prune_logs() {
    local -a logs=()
    # Filenames are ours and stamped UTC, so a reverse lexicographic sort is a
    # reverse chronological sort -- no parsing of mtimes or of `ls` output.
    mapfile -t logs < <(
        find "$LOG_DIR" -maxdepth 1 -type f -name 'nightly-*.log' -printf '%f\n' \
            | sort -r
    )
    if [ "${#logs[@]}" -le "$LOG_KEEP" ]; then
        return 0
    fi
    local old
    for old in "${logs[@]:$LOG_KEEP}"; do
        rm -f -- "$LOG_DIR/$old"
        log "pruned old log $old"
    done
}

wait_for_endpoint() {
    local deadline=$(( SECONDS + WAIT_TIMEOUT_SECS ))
    local attempt=0
    while true; do
        attempt=$(( attempt + 1 ))
        if "$CURL_BIN" -sf -m "$PROBE_TIMEOUT_SECS" -o /dev/null "$OLLAMA_PROBE_URL"; then
            log "endpoint reachable after $attempt attempt(s)"
            return 0
        fi
        if [ "$SECONDS" -ge "$deadline" ]; then
            log "endpoint still unreachable after ${WAIT_TIMEOUT_SECS}s ($attempt attempts)"
            return 1
        fi
        log "endpoint not ready (attempt $attempt), retrying in ${WAIT_INTERVAL_SECS}s"
        sleep "$WAIT_INTERVAL_SECS"
    done
}

log "nightly assurance run $RUN_STAMP"
log "repo      $REPO_DIR"
log "suite     $SUITE"
log "baseline  $BASELINE_LABEL"
log "model     $MODEL via $OPENAI_BASE_URL"
log "status    $STATUS_OUT"
log "log       $LOG_FILE"

if [ ! -x "$UV_BIN" ]; then
    log "FATAL: uv not found or not executable at $UV_BIN"
    exit 1
fi
if [ ! -f "$REPO_DIR/$SUITE" ]; then
    log "FATAL: suite $SUITE missing from $REPO_DIR"
    exit 1
fi

cd -- "$REPO_DIR" || exit 1

if ! wait_for_endpoint; then
    log "giving up: no battery was run, and the status file was left untouched"
    # Deliberately not writing a status JSON here. The file is `cli.py monitor`'s
    # record of a comparison that actually happened; a hand-written stand-in
    # would either claim has_drift=false (a clean bill of health nobody earned)
    # or invent a shape the readers of that file do not expect. Staleness is the
    # honest signal: the `checked_at` timestamp stops advancing, and this run
    # exits non-zero so systemd flags it.
    prune_logs
    exit "$EXIT_ENDPOINT_UNAVAILABLE"
fi

MONITOR_ARGS=(
    run python cli.py monitor "$SUITE"
    --baseline "$BASELINE_LABEL"
    --adapter "$ADAPTER"
    --model "$MODEL"
    --status-out "$STATUS_OUT"
)

if [ "$DRY_RUN" -eq 1 ]; then
    log "DRY RUN -- would execute:"
    log "  $UV_BIN ${MONITOR_ARGS[*]}"
    prune_logs
    log "dry run complete; no model was called and no status file was written"
    exit 0
fi

log "starting battery (this holds the GPU for several minutes)"
# errexit is lifted for exactly this call so the exit code can be captured
# instead of aborting the script mid-run and skipping the pruning below.
set +e
"$UV_BIN" "${MONITOR_ARGS[@]}"
rc=$?
set -e

prune_logs

# The CLI's exit code is passed through untouched, and this is the point of the
# whole arrangement: `monitor` exits 4 when the model has drifted from the
# baseline, and systemd records any non-zero exit as a failed unit. Swallowing
# it -- or ending with a tidy `exit 0` -- would leave `systemctl --user status`
# and the timer's history showing a healthy green run on the exact morning the
# audit found something. A drift finding must be able to fail loudly on its own.
case "$rc" in
    0) log "no drift against baseline $BASELINE_LABEL" ;;
    4) log "DRIFT DETECTED against baseline $BASELINE_LABEL -- see $STATUS_OUT" ;;
    *) log "run failed with exit code $rc -- see the log above" ;;
esac

exit "$rc"
