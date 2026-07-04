#!/bin/bash
# disk-cleanup-alert.sh — Watchdog: alert when disk cleanup is needed
# Runs via cron (no-agent). SILENT when healthy. Alerts when disk > 70%.
# Suggests running the disk-cleanup-sudo.sh script.

set -u

THRESHOLD_PCT=70
DISK_PCT=$(df / | tail -1 | awk '{print $5}' | tr -d '%')
SWAP_TOTAL_KB=$(grep SwapTotal /proc/meminfo 2>/dev/null | awk '{print $2}')
MEM_TOTAL_KB=$(grep MemTotal /proc/meminfo 2>/dev/null | awk '{print $2}')

if [ -z "$DISK_PCT" ]; then
    exit 0
fi

# Check if oversized swap exists (> 3x RAM = excessive)
SWAP_RATIO=0
if [ "$MEM_TOTAL_KB" -gt 0 ] 2>/dev/null; then
    SWAP_RATIO=$(( SWAP_TOTAL_KB / MEM_TOTAL_KB ))
fi

# Only alert if disk is over threshold
if [ "$DISK_PCT" -lt "$THRESHOLD_PCT" ]; then
    exit 0  # healthy → silent
fi

echo "DISK ALERT: ${DISK_PCT}% used"
echo ""
echo "Quick cleanup (no sudo needed):"
echo "  go clean -modcache; npm cache clean --force; rm -rf ~/.bun/install/cache"
echo ""
echo "Full cleanup (needs sudo):"
echo "  sudo bash ~/.hermes/profiles/manager/scripts/disk-cleanup-sudo.sh"
echo "  Expected recovery: 30-42 GB (dead swapfiles + journal + apt + docker)"

# Alert about oversized swap
if [ "$SWAP_RATIO" -gt 3 ]; then
    echo ""
    echo "SWAP OVERSIZED: swap is ${SWAP_RATIO}x RAM (recommended: 1-2x)"
    swapon --show
fi
