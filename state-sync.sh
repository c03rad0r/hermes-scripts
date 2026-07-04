#!/usr/bin/env bash
# state-sync-cron.sh — thin shim so the Hermes no_agent cron job can run the
# canonical state_sync.py that lives *inside* the hermes-orchestration checkout.
#
# Why this exists: the Hermes cron tool only executes scripts placed under
# ~/.hermes/scripts/ (by bare filename). state_sync.py itself must live in the
# repo checkout (~/hermes-orchestration/scripts/sync/) because its job is to
# sync live state INTO that repo and commit there. This wrapper bridges the two:
# the scheduler runs ~/.hermes/scripts/state-sync.sh, which execs the real
# script in the repo workdir.
#
# Installed (by ansible role 23-state-sync, or manually) as:
#   ~/.hermes/scripts/state-sync.sh
#
# Cron job spec (matches systemd/hermes-state-sync.{service,timer}):
#   schedule : */30 * * * *
#   workdir  : ~/hermes-orchestration
#   no_agent : true   (script-only; silent on success, alerts on non-zero exit)
#
# Exit codes propagate from state_sync.py: 0 = synced/no-changes, non-zero =
# error (the cron layer turns a non-zero exit into an alert).
set -euo pipefail

REPO="${HERMES_ORCHESTRATION_REPO:-$HOME/hermes-orchestration}"
SCRIPT="$REPO/scripts/sync/state_sync.py"

if [[ ! -f "$SCRIPT" ]]; then
    echo "state-sync: ERROR — $SCRIPT not found." >&2
    echo "            Has scripts/sync/state_sync.py been merged into $REPO?" >&2
    exit 2
fi

# Run in the repo checkout so git commits land in hermes-orchestration.
cd "$REPO"
exec python3 "$SCRIPT" "$@"
