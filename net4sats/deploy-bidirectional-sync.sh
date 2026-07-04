#!/bin/bash
# deploy-bidirectional-sync.sh — wire the upgraded bidirectional Nostr kanban
# sync into the MANAGER profile (where the cron jobs already run).
#
# WHY A SCRIPT: the upgraded scripts live in the shared ~/.hermes/scripts/net4sats/
# but the manager cron resolves bare names from ~/.hermes/profiles/manager/scripts/.
# Copying the upgraded scripts there WITH THE SAME NAMES lets the existing every-15m
# cron jobs pick them up with ZERO cron-config change.
#
# This touches the manager profile, so it is intentionally NOT run automatically by
# the worker. Review it, then run:  bash ~/.hermes/scripts/net4sats/deploy-bidirectional-sync.sh
#
# What it does:
#   1. Backs up the 2 original manager scripts (timestamped suffix)
#   2. Copies 3 upgraded files into manager/scripts/ (2 shell + kanban-nostr-replicate.py)
#   3. Verifies the shell scripts now show the "Two functions" bidirectional header
#   4. Prints next-cron-run info
#
# Reversible: restore from the .bak.<timestamp> files.
set -euo pipefail

SRC="$HOME/.hermes/scripts/net4sats"
DST="$HOME/.hermes/profiles/manager/scripts"
TS="$(date +%Y%m%d-%H%M%S)"

echo "=== bidirectional kanban-nostr sync deploy ($TS) ==="
echo "src: $SRC"
echo "dst: $DST"
echo

[ -f "$SRC/nostr-kanban-sync.sh" ]         || { echo "FAIL: missing $SRC/nostr-kanban-sync.sh"; exit 1; }
[ -f "$SRC/nostr-kanban-inbound-sync.sh" ] || { echo "FAIL: missing $SRC/nostr-kanban-inbound-sync.sh"; exit 1; }
[ -f "$SRC/kanban-nostr-replicate.py" ]    || { echo "FAIL: missing $SRC/kanban-nostr-replicate.py"; exit 1; }
mkdir -p "$DST"

# 1. Back up originals (only if not already backed up this second)
for f in nostr-kanban-sync.sh nostr-kanban-inbound-sync.sh; do
    if [ -f "$DST/$f" ] && ! head -5 "$DST/$f" | grep -q "Two functions"; then
        cp -p "$DST/$f" "$DST/$f.bak.$TS"
        echo "  backed up original $f -> $f.bak.$TS"
    fi
done

# 2. Copy upgraded files (shell scripts keep bare names; replicate.py alongside)
cp "$SRC/nostr-kanban-sync.sh"         "$DST/nostr-kanban-sync.sh"
cp "$SRC/nostr-kanban-inbound-sync.sh" "$DST/nostr-kanban-inbound-sync.sh"
cp "$SRC/kanban-nostr-replicate.py"    "$DST/kanban-nostr-replicate.py"
chmod +x "$DST/nostr-kanban-sync.sh" "$DST/nostr-kanban-inbound-sync.sh"
echo "  copied 3 upgraded files -> $DST"

# 3. Verify
echo
echo "=== verify ==="
head -7 "$DST/nostr-kanban-sync.sh" | grep -q "Two functions" \
    && echo "  OK outbound script is bidirectional" || { echo "  FAIL: outbound not upgraded"; exit 1; }
head -7 "$DST/nostr-kanban-inbound-sync.sh" | grep -q "Two functions" \
    && echo "  OK inbound script is bidirectional" || { echo "  FAIL: inbound not upgraded"; exit 1; }
python3 -c "import ast; ast.parse(open('$DST/kanban-nostr-replicate.py').read())" \
    && echo "  OK replicate.py parses" || { echo "  FAIL: replicate.py syntax error"; exit 1; }

echo
echo "=== deploy complete ==="
echo "The existing manager cron jobs (every 15m, no_agent) will run the upgraded"
echo "scripts on their next tick. No cron-config change was needed."
echo
echo "NOTE on first live run: the outbound watermark is already at 'current'"
echo "(verification dry-runs pre-consumed the seed-to-now deltas). So the first"
echo "live publish will send only NEW changes accrued after this deploy — which is"
echo "correct delta-sync behavior. DQ05 gets its full baseline via SSHFS (M2b),"
echo "then receives future deltas over Nostr. To force a full re-publish of all"
echo "current tasks instead, delete ~/.hermes/state/kanban-nostr-outbound.json"
echo "and re-run 'python3 kanban-nostr-replicate.py --seed' on each machine."
echo
echo "Relays: wss://relay.damus.io wss://nos.lol  (kind 38010, parameterized"
echo "replaceable per NIP-33; d-tag = <hostname>:<board>:<task_id>)."
echo
echo "To roll back: restore the .bak.$TS files in $DST"
