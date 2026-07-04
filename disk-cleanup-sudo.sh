#!/bin/bash
# disk-cleanup-sudo.sh — One-shot cleanup of disk space (requires sudo)
# Run manually: sudo bash disk-cleanup-sudo.sh
# Or schedule via cron if passwordless sudo is available.
#
# Estimated space recovery:
#   - swapfile2 removal:     ~32 GB (0 bytes used)
#   - swap.img removal:      ~4 GB  (19 MB used — redundant with swapfile)
#   - journal vacuum to 1G:  ~1.6 GB
#   - apt autoclean:         ~600 MB
#   - docker volume prune:   ~3.9 GB (unused volumes)
#   Total:                   ~42 GB

set -euo pipefail

echo "=== Disk cleanup starting $(date) ==="
echo "Before:"; df -h / | tail -1

# 1. Remove the 32GB swapfile2 (0 bytes used)
if swapon --show | grep -q swapfile2; then
    echo "→ Removing /swapfile2 (32GB, 0 bytes used)"
    swapoff /swapfile2
    rm -f /swapfile2
else
    echo "→ /swapfile2 not active"
fi

# 2. Remove the 4GB /swap.img (19MB used — redundant, swapfile covers it)
if swapon --show | grep -q swap.img; then
    echo "→ Removing /swap.img (4GB, 19MB used)"
    swapoff /swap.img
    rm -f /swap.img
else
    echo "→ /swap.img not active"
fi

# Update fstab to remove references
sed -i.bak '/swapfile2/d; /swap.img/d' /etc/fstab

# Remaining swap: /swapfile (16GB) + zram0 (3.5GB) = 19.5GB total
# That's still generous for a 7GB machine.

# 3. Vacuum systemd journal to 1GB
echo "→ Vacuuming journal to 1GB"
journalctl --vacuum-size=1G

# 4. APT clean
echo "→ APT autoclean/autoremove"
apt-get autoclean -y 2>/dev/null || true
apt-get autoremove -y 2>/dev/null || true

# 5. Docker volume prune (unused volumes only)
echo "→ Pruning unused Docker volumes"
docker volume prune -f 2>/dev/null || true

echo "=== Cleanup complete ==="
echo "After:"; df -h / | tail -1
echo "Swap:"; swapon --show
