#!/bin/bash
# human-gate-resolver.sh — Thin wrapper.
#
# The real scan logic now lives in human-gate-resolver.py (standalone file,
# no inline `python3 -c` — infra-doc pitfall #2 fix). This wrapper keeps
# existing callers working. Kept for backwards compatibility.
#
# Run as: hermes cron --no-agent --script scripts/human-gate-resolver.sh
# (but the cron now points directly at human-gate-resolver.py)
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$DIR/human-gate-resolver.py" "$@"
