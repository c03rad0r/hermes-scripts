#!/usr/bin/env python3
"""peak_hours_check — weekly: fetch z.ai Coding Plan docs, detect peak-hours changes, update config.

Watchdog: silent if unchanged; alerts (stdout) when peak hours/multipliers shift.
Reads/writes ~/.hermes/bot/peak_hours.json. peak_alert.sh + throttled_daemon.sh
read the same JSON, so an update takes effect on their next run (no restart needed).
"""
from __future__ import annotations
import json, re, sys, time, urllib.request
from pathlib import Path

CONFIG = Path.home() / ".hermes" / "bot" / "peak_hours.json"
DOCS_URL = "https://docs.z.ai/devpack/overview"


def fetch_docs() -> str:
    with urllib.request.urlopen(DOCS_URL, timeout=20) as r:
        return r.read().decode(errors="ignore")


def parse_peak(text: str) -> dict | None:
    m = re.search(r"Peak hours are (\d{1,2}):00[–\-](\d{1,2}):00\s*\(UTC\+(\d+)\)", text)
    if not m:
        return None
    sl, el, tz = int(m[1]), int(m[2]), int(m[3])
    return {"peak_start_utc": (sl - tz) % 24, "peak_end_utc": (el - tz) % 24,
            "peak_local": f"{sl}:00-{el}:00 UTC+{tz}"}


def parse_mult(text: str, label: str) -> int | None:
    m = re.search(rf"(\d+)\s*[×x]\s*during\s*{label}", text, re.IGNORECASE)
    return int(m[1]) if m else None


def main() -> int:
    current = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    try:
        text = fetch_docs()
    except Exception as e:
        return 0  # transient: silent
    current["last_checked"] = int(time.time())

    new = {}
    if p := parse_peak(text):
        new.update(p)
    if pm := parse_mult(text, "peak"):
        new["peak_multiplier"] = pm
    if om := parse_mult(text, "off.?peak"):
        new["offpeak_multiplier"] = om

    changed = []
    for k, v in new.items():
        if v != current.get(k):
            changed.append(f"{k}: {current.get(k, '?')} → {v}")
            current[k] = v

    CONFIG.write_text(json.dumps(current, indent=2))
    if changed:
        print(f"🔄 PEAK HOURS UPDATE — z.ai changed their terms:")
        for c in changed:
            print(f"  • {c}")
        print(f"  Config updated: {CONFIG}")
        print(f"  peak_alert + dispatcher will use the new values on their next run.")
    # else: silent (unchanged)
    return 0


if __name__ == "__main__":
    sys.exit(main())
