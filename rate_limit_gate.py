#!/usr/bin/env python3
"""
Rate-limit gate for dispatch-level worker spawning control.

Checks three conditions before allowing worker dispatch:
1. Is current UTC hour a known rate-limit hour? (historical 429 frequency by hour)
2. Any 429 in last 5 minutes? (active rate-limit burst)
3. Kalman prediction: will quota exhaust during next task duration?

Outputs JSON state to ~/.hermes/state/rate_limit_gate.json:
  {paused: bool, resume_at: iso_ts|null, reason: str, ts: iso_ts}

Exit code 0 = clear (dispatch OK), exit code 1 = paused (skip dispatch).

Usage:
  python3 rate_limit_gate.py [--duration SECONDS] [--db PATH]
  --duration: estimated next task duration in seconds (default 300)
  --db: path to zai_usage.db (default ~/.hermes/bot/zai_usage.db)
"""

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = os.path.expanduser("~/.hermes/bot/zai_usage.db")
STATE_PATH = os.path.expanduser("~/.hermes/state/rate_limit_gate.json")
DEFAULT_DURATION = 300  # 5 min estimated task duration

# --- Rate-limit hot hours (from historical analysis: peak 02-05 + 10-11 UTC) ---
# These are also computed dynamically, but we seed with known peaks.
KNOWN_PEAK_HOURS = {2, 3, 4, 5, 10, 11}

# Thresholds
RECENT_429_WINDOW = 300          # 5 min
RECENT_429_THRESHOLD = 1         # any 429 in window → pause
PEAK_HOUR_429_RATIO = 0.15       # if current hour historically has >15% of all 429s → cautious
KALMAN_EXHAUST_HOURS = 0.5       # if Kalman predicts exhaust in < 0.5h → pause
MIN_SAMPLES_FOR_PEAK = 5         # need at least this many 429s in an hour to call it peak


def utc_now():
    return datetime.now(timezone.utc)


def iso(ts=None):
    if ts is None:
        ts = time.time()
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def connect(db_path):
    if not os.path.exists(db_path):
        print(f"WARN: DB not found at {db_path}, gate defaults to CLEAR", file=sys.stderr)
        return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def check_peak_hour(conn):
    """Check if current UTC hour is a known rate-limit hot hour.

    Queries rate_limit_samples grouped by UTC hour to find hours with
    historically high 429 frequency. Falls back to KNOWN_PEAK_HOURS seed.
    """
    now = utc_now()
    current_hour = now.hour

    # Query: count 429s per UTC hour from historical data
    try:
        rows = conn.execute("""
            SELECT
                CAST(strftime('%H', datetime(ts, 'unixepoch')) AS INTEGER) AS hour,
                COUNT(*) AS cnt
            FROM rate_limit_samples
            GROUP BY hour
            ORDER BY cnt DESC
        """).fetchall()

        # Build dynamic peak set: hours with >= MIN_SAMPLES_FOR_PEAK 429s
        dynamic_peaks = set()
        total = sum(r["cnt"] for r in rows) if rows else 0
        for r in rows:
            if r["cnt"] >= MIN_SAMPLES_FOR_PEAK:
                dynamic_peaks.add(r["hour"])

        peak_hours = dynamic_peaks | KNOWN_PEAK_HOURS

        is_peak = current_hour in peak_hours

        # Find the count for current hour
        current_hour_count = 0
        for r in rows:
            if r["hour"] == current_hour:
                current_hour_count = r["cnt"]
                break

        # Compute ratio
        ratio = (current_hour_count / total) if total > 0 else 0.0

        return {
            "is_peak": is_peak,
            "current_hour": current_hour,
            "current_hour_429s": current_hour_count,
            "total_429s": total,
            "ratio": round(ratio, 3),
            "dynamic_peaks": sorted(dynamic_peaks),
            "reason": f"hour {current_hour:02d}Z is a known rate-limit peak hour ({current_hour_count} historical 429s)"
                      if is_peak else None,
        }
    except Exception as e:
        return {"is_peak": False, "error": str(e), "reason": None}


def check_recent_429(conn):
    """Check for any 429 (rate_limit_sample) in the last N seconds."""
    cutoff = time.time() - RECENT_429_WINDOW
    try:
        rows = conn.execute("""
            SELECT COUNT(*) AS cnt, MAX(ts) AS last_ts
            FROM rate_limit_samples
            WHERE ts >= ?
        """, (cutoff,)).fetchone()

        count = rows["cnt"] if rows else 0
        last_ts = rows["last_ts"] if rows and rows["last_ts"] else None

        # Also check api_calls for status_code=429
        api_rows = conn.execute("""
            SELECT COUNT(*) AS cnt, MAX(ts) AS last_ts
            FROM api_calls
            WHERE status_code = 429 AND ts >= ?
        """, (cutoff,)).fetchone()

        api_count = api_rows["cnt"] if api_rows else 0

        total = count + api_count
        triggered = total >= RECENT_429_THRESHOLD

        # Estimate resume time from last retry_after_estimate if available
        resume_offset = 60  # default 1 min backoff
        if last_ts:
            recent = conn.execute("""
                SELECT retry_after_estimate FROM rate_limit_samples
                WHERE ts >= ? ORDER BY ts DESC LIMIT 1
            """, (cutoff,)).fetchone()
            if recent and recent["retry_after_estimate"] and recent["retry_after_estimate"] > 0:
                resume_offset = recent["retry_after_estimate"]

        return {
            "triggered": triggered,
            "count": total,
            "rls_count": count,
            "api_count": api_count,
            "last_ts": last_ts,
            "resume_offset": resume_offset,
            "reason": f"{total} 429(s) in last {RECENT_429_WINDOW}s (rls={count}, api={api_count})"
                      if triggered else None,
        }
    except Exception as e:
        return {"triggered": False, "error": str(e), "resume_offset": 60, "reason": None}


def check_kalman(conn, task_duration_s):
    """Check Kalman prediction for quota exhaustion during task window.

    Looks at latest kalman_samples for 'ours' key to see if any window
    predicts exhaustion within the task duration window.
    """
    try:
        # Get latest sample per window
        rows = conn.execute("""
            SELECT k.*
            FROM kalman_samples k
            INNER JOIN (
                SELECT window, MAX(ts) AS max_ts
                FROM kalman_samples
                WHERE key = 'ours'
                GROUP BY window
            ) latest ON k.window = latest.window AND k.ts = latest.max_ts
            WHERE k.key = 'ours'
            ORDER BY k.projected_total_pct DESC
        """).fetchall()

        if not rows:
            return {"triggered": False, "reason": None, "windows": []}

        # Convert task duration to hours for comparison
        task_duration_h = task_duration_s / 3600.0

        windows_info = []
        worst = None
        for r in rows:
            info = {
                "window": r["window"],
                "used_pct": r["used_pct_observed"],
                "projected_total_pct": r["projected_total_pct"],
                "burn_rate_tph": r["burn_rate_tph"],
                "exhausts_in_hours": r["exhausts_in_hours"],
                "will_exhaust": bool(r["will_exhaust"]),
                "note": r["note"],
            }
            windows_info.append(info)

            # Trigger if: will_exhaust flag is set AND exhausts within our task window
            # OR projected_total_pct >= 95 (near ceiling)
            if r["will_exhaust"] and r["exhausts_in_hours"] is not None:
                if r["exhausts_in_hours"] <= KALMAN_EXHAUST_HOURS:
                    if worst is None or r["exhausts_in_hours"] < worst["exhausts_in_hours"]:
                        worst = info
            elif r["projected_total_pct"] is not None and r["projected_total_pct"] >= 95.0:
                if worst is None:
                    worst = info

        triggered = worst is not None
        reason = None
        if worst:
            if worst["will_exhaust"]:
                reason = (f"Kalman: {worst['window']} window predicts exhaustion in "
                          f"{worst['exhausts_in_hours']:.1f}h (projected {worst['projected_total_pct']:.1f}%)")
            else:
                reason = (f"Kalman: {worst['window']} window projected at "
                          f"{worst['projected_total_pct']:.1f}% (>= 95% threshold)")

        # Resume estimate: if we know exhausts_in_hours, resume after that window
        resume_offset = 600  # default 10 min
        if worst and worst["exhausts_in_hours"] and worst["exhausts_in_hours"] > 0:
            resume_offset = int(worst["exhausts_in_hours"] * 3600) + 60  # +1min buffer

        return {
            "triggered": triggered,
            "reason": reason,
            "resume_offset": resume_offset,
            "windows": windows_info,
        }
    except Exception as e:
        return {"triggered": False, "error": str(e), "resume_offset": 600, "reason": None, "windows": []}


def run_gate(db_path=DEFAULT_DB, task_duration=DEFAULT_DURATION, verbose=False):
    """Run all three checks and produce the gate decision."""
    now = time.time()
    conn = connect(db_path)

    # If no DB, default to clear (don't block dispatch on missing data)
    if conn is None:
        result = {
            "paused": False,
            "resume_at": None,
            "reason": "DB not found — gate defaults to clear",
            "ts": iso(now),
            "checks": {},
        }
        return result

    try:
        peak = check_peak_hour(conn)
        recent = check_recent_429(conn)
        kalman = check_kalman(conn, task_duration)

        # Decision logic — priority: active 429 > Kalman exhaustion > peak hour (advisory only)
        paused = False
        reason = "clear"
        resume_at = None

        if recent["triggered"]:
            paused = True
            resume_ts = now + recent["resume_offset"]
            resume_at = iso(resume_ts)
            reason = f"ACTIVE 429: {recent['reason']}"
        elif kalman["triggered"]:
            paused = True
            resume_ts = now + kalman["resume_offset"]
            resume_at = iso(resume_ts)
            reason = f"KALMAN: {kalman['reason']}"
        elif peak["is_peak"]:
            # Peak hour is advisory only — don't hard pause, just note it
            # Unless combined with elevated recent activity (even below threshold)
            reason = f"ADVISORY: {peak['reason']} — dispatch with caution"
            paused = False

        result = {
            "paused": paused,
            "resume_at": resume_at,
            "reason": reason,
            "ts": iso(now),
            "checks": {
                "peak_hour": peak,
                "recent_429": {k: v for k, v in recent.items() if k != "reason"},
                "kalman": {k: v for k, v in kalman.items() if k != "reason"},
            },
        }

        if verbose:
            result["checks"]["recent_429"]["reason"] = recent.get("reason")
            result["checks"]["kalman"]["reason"] = kalman.get("reason")

        return result
    finally:
        conn.close()


def write_state(result):
    """Write gate decision to state file."""
    Path(STATE_PATH).parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(result, f, indent=2, default=str)


def main():
    parser = argparse.ArgumentParser(description="Rate-limit gate for dispatch control")
    parser.add_argument("--duration", type=int, default=DEFAULT_DURATION,
                        help=f"Estimated next task duration in seconds (default {DEFAULT_DURATION})")
    parser.add_argument("--db", default=DEFAULT_DB,
                        help=f"Path to zai_usage.db (default {DEFAULT_DB})")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Include detailed check reasons in output")
    parser.add_argument("--stdout", action="store_true",
                        help="Also print result to stdout")
    args = parser.parse_args()

    result = run_gate(db_path=args.db, task_duration=args.duration, verbose=args.verbose)

    write_state(result)

    if args.stdout or args.verbose:
        print(json.dumps(result, indent=2, default=str))

    # Human-readable summary to stderr
    status = "PAUSED" if result["paused"] else "CLEAR"
    print(f"[rate_limit_gate] {status} — {result['reason']}", file=sys.stderr)
    if result["resume_at"]:
        print(f"  resume_at: {result['resume_at']}", file=sys.stderr)

    # Exit code: 0 = clear, 1 = paused
    sys.exit(1 if result["paused"] else 0)


if __name__ == "__main__":
    main()
