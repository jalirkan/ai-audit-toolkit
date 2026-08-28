#!/usr/bin/env bash
#
# Install the nightly assurance timer into the current user's systemd.
#
# Safe to run repeatedly: every step here is idempotent, so this is also the
# right thing to run after editing a unit file or moving the repo.
#
# Usage:
#   ops/install-nightly.sh              install and enable the timer
#   ops/install-nightly.sh --no-enable  link the units only, arm nothing
#   ops/install-nightly.sh --uninstall  disable the timer and remove the links
#   ops/install-nightly.sh --help

set -euo pipefail

export PATH="/usr/local/bin:/usr/bin:/bin"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
UNIT_SRC_DIR="$SCRIPT_DIR/systemd"
UNIT_DEST_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
RUNNER="$SCRIPT_DIR/nightly-audit.sh"

SERVICE_UNIT="ai-audit-nightly.service"
TIMER_UNIT="ai-audit-nightly.timer"

ENABLE=1
UNINSTALL=0

usage() {
    sed -n '3,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
    case "$1" in
        --no-enable) ENABLE=0 ;;
        --uninstall) UNINSTALL=1 ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'install-nightly: unknown argument %q\n' "$1" >&2; exit 2 ;;
    esac
    shift
done

# Running this under sudo would install the units into root's systemd instance,
# where they would never run and would be tedious to find again. Better to stop
# than to succeed somewhere useless.
if [ "$(id -u)" -eq 0 ]; then
    echo "Refusing to run as root: these are user units and belong to your own" >&2
    echo "systemd instance. Run it as yourself, without sudo." >&2
    exit 1
fi

# `systemctl --user` needs a session bus to talk to. Over a bare SSH session or
# in some containers there is not one, and the failure mode is an opaque
# "Failed to connect to bus" several steps later.
if ! systemctl --user show-environment >/dev/null 2>&1; then
    echo "Cannot reach your user systemd instance (no session bus)." >&2
    echo "Run this from a desktop session or a login shell on this machine." >&2
    exit 1
fi

if [ "$UNINSTALL" -eq 1 ]; then
    # `disable --now` on an already-disabled unit is a no-op, so no guards.
    systemctl --user disable --now "$TIMER_UNIT" >/dev/null 2>&1 || true
    rm -f -- "$UNIT_DEST_DIR/$TIMER_UNIT" "$UNIT_DEST_DIR/$SERVICE_UNIT"
    systemctl --user daemon-reload
    echo "Removed $TIMER_UNIT and $SERVICE_UNIT from $UNIT_DEST_DIR."
    echo "Nothing under ~/.local/state/ai-audit was touched; logs and the"
    echo "status file are evidence and are left alone."
    exit 0
fi

for unit in "$SERVICE_UNIT" "$TIMER_UNIT"; do
    if [ ! -f "$UNIT_SRC_DIR/$unit" ]; then
        echo "Missing unit file: $UNIT_SRC_DIR/$unit" >&2
        exit 1
    fi
done

if [ ! -x "$RUNNER" ]; then
    echo "Runner is not executable: $RUNNER" >&2
    echo "Fix with: chmod +x $RUNNER" >&2
    exit 1
fi

mkdir -p -- "$UNIT_DEST_DIR"

# Symlinked rather than copied so the repo stays the single source of truth: a
# `git pull` that changes the schedule takes effect after a daemon-reload,
# instead of leaving an edited unit in the repo and a stale copy in ~/.config
# disagreeing about when the battery runs. The trade is that moving or deleting
# the repo breaks the units -- rerun this script after any move.
for unit in "$SERVICE_UNIT" "$TIMER_UNIT"; do
    ln -sfn -- "$UNIT_SRC_DIR/$unit" "$UNIT_DEST_DIR/$unit"
    echo "linked $UNIT_DEST_DIR/$unit -> $UNIT_SRC_DIR/$unit"
done

systemctl --user daemon-reload
echo "reloaded the user systemd manager"

if [ "$ENABLE" -eq 1 ]; then
    # --now starts the timer as well as enabling it, so the schedule is armed
    # immediately rather than at the next login. Starting a *timer* only arms
    # it; it does not run the battery.
    systemctl --user enable --now "$TIMER_UNIT"
    echo "enabled and started $TIMER_UNIT"
else
    echo "skipped enabling (--no-enable); the timer is linked but not armed"
fi

cat <<EOF

Done. To check on it:

  # when it will next fire, and when it last did
  systemctl --user list-timers ai-audit-nightly.timer

  # the timer's and the service's current state
  systemctl --user status ai-audit-nightly.timer
  systemctl --user status ai-audit-nightly.service

  # output of the most recent run
  journalctl --user -u ai-audit-nightly.service -n 50 --no-pager

  # the per-run logs and the machine-readable status
  ls -lt ~/.local/state/ai-audit/logs/ | head
  cat ~/.local/state/ai-audit/monitor-status.json

To rehearse the plumbing without calling the model (no GPU, no battery):

  $RUNNER --dry-run

To force one real run right now (holds the GPU for several minutes):

  systemctl --user start ai-audit-nightly.service

One thing worth knowing: user timers only run while your user manager is
running, which normally means while you are logged in. If you want the nightly
run to happen on a night you are logged out, enable lingering once:

  loginctl enable-linger \$USER

A red 'failed' state on ai-audit-nightly.service is not necessarily a broken
job. Exit code 4 means the battery ran fine and found drift against the
baseline -- that is the alarm working. Exit code 75 means the ollama endpoint
never came up, so no battery ran at all and the status file is stale.
EOF
