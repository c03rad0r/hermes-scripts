#!/usr/bin/env bash
# peak_alert.sh — config-driven. Reads peak_hours.json (updated weekly by peak_hours_check.py).
PEAK_CFG="$HOME/.hermes/bot/peak_hours.json"
PY="$(command -v python3 || echo "$HOME/.hermes/hermes-agent/venv/bin/python")"
ps=$($PY -c "import json;d=json.load(open('$PEAK_CFG'));print(d.get('peak_start_utc',6))" 2>/dev/null||echo 6)
pe=$($PY -c "import json;d=json.load(open('$PEAK_CFG'));print(d.get('peak_end_utc',10))" 2>/dev/null||echo 10)
mult=$($PY -c "import json;d=json.load(open('$PEAK_CFG'));print(d.get('peak_multiplier',3))" 2>/dev/null||echo 3)
loc=$($PY -c "import json;d=json.load(open('$PEAK_CFG'));print(d.get('peak_local','14:00-18:00 UTC+8'))" 2>/dev/null||echo "14:00-18:00 UTC+8")

hour=$(date -u +%H)
if [ "$hour" -ge "$ps" ] && [ "$hour" -lt "$pe" ]; then
  echo "⚠️ PEAK HOURS NOW ACTIVE ($loc). GLM-5.2 burns ${mult}× quota. Dispatch PAUSED until ${pe}:00 UTC."
  echo "Interactive chat still works (uses fallback key)."
else
  echo "✅ Peak hours ended. Off-peak rates resumed. Dispatch resuming."
fi
