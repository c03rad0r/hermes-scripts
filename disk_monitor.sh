#!/usr/bin/env bash
# Hermes watchdog: disk-space monitor (responsibility #9)
# Pattern: --no-agent cron script. stdout delivered verbatim; EMPTY = silent.
# Alerts only when free space is low OR actionable cleanup opportunities exist.
#
# Schedule suggestion: hermes cron create --no-agent --script disk_monitor.sh \
#                   --name disk-watch --deliver local '30m'

set -u

# --- tunables (env-overridable) ---
WARN_FREE_GB="${DISK_WARN_FREE_GB:-15}"          # alert if free < this
WARN_FREE_PCT="${DISK_WARN_FREE_PCT:-92}"        # alert if use% >= this
CACHE_REGROW_MB="${DISK_CACHE_REGROW_MB:-800}"   # alert if regrown caches > this
KERNEL_KEEP="${DISK_KERNEL_KEEP:-2}"             # running + this many fallbacks

alerts=()

# 1) Free space
read -r use_pct avail_kb _ < <(df --output=pcent,avail / | tail -1 | tr -d '%')
avail_gb=$(( avail_kb / 1024 / 1024 ))
if [ "$avail_gb" -lt "$WARN_FREE_GB" ] || [ "$use_pct" -ge "$WARN_FREE_PCT" ]; then
  alerts+=("LOW DISK: ${avail_gb}G free (${use_pct}% used) on /")
fi

# 2) Regrown safe-to-clear caches (seed list from cleanup Tier 1)
cache_total=0
cache_hits=()
for d in \
  "$HOME/.cache/ms-playwright" \
  "$HOME/.cache/tracker3" \
  "$HOME/.cache/go-build" \
  "$HOME/.npm" \
  "$HOME/.cargo/registry/cache" \
  "$HOME/.cache/pip" \
  "$HOME/.cache/deno"
do
  if [ -e "$d" ]; then
    sz_kb=$(du -sk "$d" 2>/dev/null | cut -f1)
    cache_total=$(( cache_total + sz_kb ))
    sz_mb=$(( sz_kb / 1024 ))
    [ "$sz_mb" -ge 150 ] && cache_hits+=("$(basename "$d") ${sz_mb}M")
  fi
done
cache_total_mb=$(( cache_total / 1024 ))
if [ "$cache_total_mb" -ge "$CACHE_REGROW_MB" ]; then
  alerts+=("CACHE REGROWTH: ~${cache_total_mb}M reclaimable ($(IFS=','; echo "${cache_hits[*]}")). Safe to clear.")
fi

# 3) Disabled (stale) snap revisions
if command -v snap >/dev/null 2>&1; then
  disabled=$(snap list --all 2>/dev/null | awk '/disabled/{print $1, $3}')
  if [ -n "$disabled" ]; then
    alerts+=("STALE SNAPS (disabled): $(echo "$disabled" | tr '\n' ',' | sed 's/,$//'). Run: sudo snap remove <name> --revision=<rev>")
  fi
fi

# 4) Old kernels (keep running + $KERNEL_KEEP fallbacks)
if command -v dpkg >/dev/null 2>&1; then
  running=$(uname -r)
  img_count=$(dpkg -l 2>/dev/null | awk '/^ii[[:space:]]+linux-image-[0-9]/{c++} END{print c+0}')
  extra=$(( img_count - 1 - KERNEL_KEEP ))
  if [ "$extra" -gt 0 ]; then
    alerts+=("OLD KERNELS: ${img_count} installed (running ${running}); ~${extra} beyond ${KERNEL_KEEP} fallback(s). sudo apt purge old ones")
  fi
fi

# 5) opencode.db growth watch (history — valuable, NOT auto-cleared)
odb="$HOME/.local/share/opencode/opencode.db"
if [ -f "$odb" ]; then
  odb_gb=$(awk 'BEGIN{printf "%.1f", '$(stat -c %s "$odb")'/1073741824}')
  if awk 'BEGIN{exit !('"${odb_gb}"' >= 3.0)}'; then
    alerts+=("OPENCODE.DB: ${odb_gb}G history DB growing (valuable — consider archive/compact, not delete)")
  fi
fi

# --- emit only if actionable ---
if [ "${#alerts[@]}" -gt 0 ]; then
  echo "💾 DISK WATCH ($(date -u +%FT%TZ))"
  for a in "${alerts[@]}"; do echo "  • $a"; done
fi
# else: silent (empty stdout)
