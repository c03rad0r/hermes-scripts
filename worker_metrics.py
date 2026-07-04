#!/usr/bin/env python3
"""
worker_metrics.py — System resource + worker metrics collector.

Records a snapshot of system state every time the watchdog cron runs.
Stores in SQLite for time-series plotting.

Schema:
  worker_metrics(
    ts REAL,            -- epoch timestamp
    load1 REAL,         -- 1-min load average
    load5 REAL,         -- 5-min load average
    load_per_core REAL, -- load1 / nproc
    mem_pct INT,        -- RAM used %
    mem_avail_mb INT,   -- RAM available (MB)
    swap_used_kb INT,   -- swap used (KB)
    swap_pct REAL,      -- swap used %
    workers INT,        -- running worker count
    max_concurrent INT, -- dynamic concurrency target (v5)
    api_quota_pct INT,  -- z.ai session quota %
    api_throttle INT,   -- z.ai throttle flag (0/1)
    tasks_ready INT,    -- ready tasks across all boards
    tasks_running INT,  -- running tasks across all boards
    tasks_blocked INT,  -- blocked tasks across all boards
    tasks_done INT      -- done tasks (cumulative since last archive)
  )

Usage:
  # Collect one snapshot (called by cron or watchdog)
  python3 worker_metrics.py

  # Export to CSV
  python3 worker_metrics.py --csv > metrics.csv

  # Show latest snapshot
  python3 worker_metrics.py --latest

  # Show summary stats
  python3 worker_metrics.py --stats

Design:
  - Zero API cost (pure local system inspection)
  - <100ms execution time
  - SQLite is append-only, auto-vacuumed weekly by existing cron
  - ~200 bytes per row, every 3 min = ~96KB/day
"""

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

DB_PATH = Path.home() / ".hermes" / "bot" / "worker_metrics.db"
BOARDS = ["plebeian", "tollgate", "admin"]
HK_TIMEOUT = 15  # seconds for hermes kanban calls

def read_loadavg():
    """Read 1-min and 5-min load averages."""
    try:
        with open("/proc/loadavg") as f:
            parts = f.read().split()
            return float(parts[0]), float(parts[1])
    except Exception:
        return 0.0, 0.0

def read_nproc():
    try:
        return int(subprocess.check_output(["nproc"]).strip())
    except Exception:
        return 4

def read_meminfo():
    """Read memory info from /proc/meminfo."""
    info = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    info[parts[0].rstrip(":")] = int(parts[1])
    except Exception:
        pass
    return info

def read_zai_state():
    """Read z.ai quota state for both keys.

    Returns (our_peak_pct, friend_pct, throttle, friend_pause).
    our_peak_pct = max(session_pct, token_pct) — the real consumption.
    """
    state_file = Path.home() / ".hermes" / "bot" / "zai_state.json"
    if not state_file.exists():
        return 0, 0, 0, 0
    try:
        with open(state_file) as f:
            data = json.load(f)
        our_peak = max(
            int(data.get("session_pct", 0)),
            int(data.get("token_pct", 0)),
        )
        friend_pct = int(data.get("friend_token_pct", 0))
        throttle = 1 if data.get("throttle", False) else 0
        friend_pause = 1 if data.get("friend_pause", False) else 0
        return our_peak, friend_pct, throttle, friend_pause
    except Exception:
        return 0, 0, 0, 0

def read_dynamic_max():
    """Call compute_max_workers.py for the dynamic concurrency target."""
    script = Path.home() / ".hermes" / "profiles" / "manager" / "scripts" / "compute_max_workers.py"
    if not script.exists():
        return 2  # fallback
    try:
        result = subprocess.check_output(
            ["python3", str(script)],
            timeout=5,
            stderr=subprocess.DEVNULL,
        ).strip()
        val = int(result)
        return val if val >= 1 else 2
    except Exception:
        return 2

def count_board_tasks():
    """Count tasks by status across all boards."""
    counts = {"ready": 0, "running": 0, "blocked": 0, "done": 0}
    for board in BOARDS:
        try:
            listing = subprocess.check_output(
                ["timeout", str(HK_TIMEOUT), "hermes", "kanban", "--board", board, "ls"],
                stderr=subprocess.DEVNULL,
                timeout=HK_TIMEOUT + 2,
            ).decode()
        except Exception:
            continue
        for line in listing.splitlines():
            line_lower = line.lower()
            for status in counts:
                if f" {status} " in line_lower or f"\t{status}\t" in line_lower:
                    counts[status] += 1
                    break
    return counts

def collect():
    """Collect one snapshot and insert into DB."""
    # System signals
    load1, load5 = read_loadavg()
    nproc = read_nproc()
    lpc = load1 / nproc if nproc > 0 else load1

    meminfo = read_meminfo()
    mem_total_kb = meminfo.get("MemTotal", 7 * 1024 * 1024)
    mem_avail_kb = meminfo.get("MemAvailable", 0)
    mem_used_kb = mem_total_kb - mem_avail_kb
    mem_pct = int(mem_used_kb / mem_total_kb * 100) if mem_total_kb > 0 else 0
    mem_avail_mb = mem_avail_kb // 1024

    swap_total_kb = meminfo.get("SwapTotal", 0)
    swap_free_kb = meminfo.get("SwapFree", 0)
    swap_used_kb = swap_total_kb - swap_free_kb
    swap_pct = (swap_used_kb / swap_total_kb * 100) if swap_total_kb > 0 else 0

    # API signals
    api_pct, friend_pct, api_throttle, friend_pause = read_zai_state()

    # Dynamic concurrency
    max_concurrent = read_dynamic_max()

    # Kanban board state
    task_counts = count_board_tasks()

    # Worker process count
    try:
        worker_count = int(subprocess.check_output(
            ["ps", "aux"],
            stderr=subprocess.DEVNULL,
        ).decode().count("kanban task"))
        # Each worker has 2 processes (parent+child), so divide by 2 roughly
        # Actually count unique task IDs
        worker_count = len(set(
            line.split("kanban task ")[-1].split()[0]
            for line in subprocess.check_output(
                ["ps", "aux"], stderr=subprocess.DEVNULL
            ).decode().splitlines()
            if "kanban task " in line
        ))
    except Exception:
        worker_count = 0

    ts = time.time()

    # Insert into DB
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS worker_metrics (
            ts REAL,
            load1 REAL,
            load5 REAL,
            load_per_core REAL,
            mem_pct INT,
            mem_avail_mb INT,
            swap_used_kb INT,
            swap_pct REAL,
            workers INT,
            max_concurrent INT,
            api_quota_pct INT,
            api_throttle INT,
            api_quota_friend_pct INT,
            tasks_ready INT,
            tasks_running INT,
            tasks_blocked INT,
            tasks_done INT
        )
    """)
    # Migration: add friend column to existing DBs
    try:
        conn.execute("ALTER TABLE worker_metrics ADD COLUMN api_quota_friend_pct INT DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # column already exists
    conn.execute(
        """INSERT INTO worker_metrics VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (ts, load1, load5, lpc, mem_pct, mem_avail_mb,
         swap_used_kb, swap_pct, worker_count, max_concurrent,
         api_pct, api_throttle, friend_pct,
         task_counts["ready"], task_counts["running"],
         task_counts["blocked"], task_counts["done"]),
    )
    conn.commit()
    conn.close()

    return {
        "ts": ts,
        "load1": load1,
        "load_per_core": lpc,
        "mem_pct": mem_pct,
        "mem_avail_mb": mem_avail_mb,
        "swap_pct": swap_pct,
        "workers": worker_count,
        "max_concurrent": max_concurrent,
        "api_quota_pct": api_pct,
        "tasks_ready": task_counts["ready"],
        "tasks_running": task_counts["running"],
    }


def export_csv():
    """Export all rows to CSV on stdout."""
    if not DB_PATH.exists():
        print("No metrics database found", file=sys.stderr)
        sys.exit(1)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM worker_metrics ORDER BY ts"
    ).fetchall()
    conn.close()

    if not rows:
        print("No data", file=sys.stderr)
        sys.exit(1)

    cols = [d[0] for d in conn.execute("SELECT * FROM worker_metrics LIMIT 0").description]
    print(",".join(cols))
    for row in rows:
        print(",".join(str(row[c]) for c in cols))


def show_latest():
    """Print the most recent snapshot."""
    if not DB_PATH.exists():
        print("No metrics database found")
        return
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM worker_metrics ORDER BY ts DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if not row:
        print("No data")
        return

    from datetime import datetime
    dt = datetime.fromtimestamp(row["ts"]).strftime("%Y-%m-%d %H:%M:%S")
    print(f"=== Latest snapshot: {dt} ===")
    print(f"Load:       {row['load1']:.2f} ({row['load_per_core']:.2f}/core)")
    print(f"RAM:        {row['mem_pct']}% used, {row['mem_avail_mb']}MB avail")
    print(f"Swap:       {row['swap_pct']:.0f}%")
    print(f"Workers:    {row['workers']}/{row['max_concurrent']} (dynamic max)")
    print(f"API quota:  {row['api_quota_pct']}%{' [THROTTLED]' if row['api_throttle'] else ''}")
    print(f"Tasks:      {row['tasks_running']} running, {row['tasks_ready']} ready, "
          f"{row['tasks_blocked']} blocked, {row['tasks_done']} done")


def show_stats():
    """Print summary statistics."""
    if not DB_PATH.exists():
        print("No metrics database found")
        return
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute("""
        SELECT
            COUNT(*) as samples,
            MIN(ts) as first_ts,
            MAX(ts) as last_ts,
            AVG(load1) as avg_load,
            MAX(load1) as max_load,
            AVG(mem_pct) as avg_mem,
            MAX(mem_pct) as max_mem,
            AVG(workers) as avg_workers,
            MAX(workers) as max_workers,
            AVG(max_concurrent) as avg_max,
            AVG(api_quota_pct) as avg_api
        FROM worker_metrics
    """).fetchone()
    conn.close()

    if not row or row[0] == 0:
        print("No data")
        return

    from datetime import datetime
    first = datetime.fromtimestamp(row[1]).strftime("%Y-%m-%d %H:%M")
    last = datetime.fromtimestamp(row[2]).strftime("%Y-%m-%d %H:%M")
    hours = (row[2] - row[1]) / 3600

    print(f"=== Worker Metrics Summary ===")
    print(f"Samples:    {row[0]} over {hours:.1f}h ({first} → {last})")
    print(f"Load:       avg={row[3]:.2f}, max={row[4]:.2f}")
    print(f"RAM:        avg={row[5]:.0f}%, max={row[6]:.0f}%")
    print(f"Workers:    avg={row[7]:.1f}, max={row[8]}")
    print(f"Max target: avg={row[9]:.1f}")
    print(f"API quota:  avg={row[10]:.0f}%")


if __name__ == "__main__":
    if "--csv" in sys.argv:
        export_csv()
    elif "--latest" in sys.argv:
        show_latest()
    elif "--stats" in sys.argv:
        show_stats()
    else:
        # Default: collect a snapshot
        result = collect()
        # Print compact summary (for watchdog-style log)
        from datetime import datetime
        dt = datetime.now().strftime("%H:%M:%S")
        print(f"[{dt}] Load={result['load1']:.1f} RAM={result['mem_pct']}% "
              f"Workers={result['workers']}/{result['max_concurrent']} "
              f"API={result['api_quota_pct']}% "
              f"Ready={result['tasks_ready']} Run={result['tasks_running']}")
