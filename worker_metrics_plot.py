#!/usr/bin/env python3
"""
worker_metrics_plot.py — Plot worker metrics from SQLite using Plotly.

Generates an interactive HTML dashboard with multiple subplots:
  1. Load average (1-min) + load-per-core
  2. Memory % + available MB
  3. Workers running vs dynamic max concurrent
  4. API quota % + throttle events
  5. Kanban task counts (ready/running/blocked/done)

Usage:
  python3 worker_metrics_plot.py                    # → ~/reports/worker-dashboard.html
  python3 worker_metrics_plot.py --hours 6          # last 6 hours
  python3 worker_metrics_plot.py --out /tmp/dash.html
  python3 worker_metrics_plot.py --csv metrics.csv  # from CSV instead of DB
"""

import argparse
import csv
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
except ImportError:
    print("Error: plotly not installed. Run: uv pip install plotly --python "
          "/home/c03rad0r/.hermes/hermes-agent/venv/bin/python3", file=sys.stderr)
    sys.exit(1)

DB_PATH = Path.home() / ".hermes" / "bot" / "worker_metrics.db"
DEFAULT_OUT = Path.home() / "reports" / "worker-dashboard.html"


def load_from_db(hours=None):
    """Load metrics from SQLite, optionally filtered to last N hours."""
    if not DB_PATH.exists():
        print(f"No metrics DB at {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    query = "SELECT * FROM worker_metrics"
    params = ()
    if hours:
        cutoff = datetime.now().timestamp() - hours * 3600
        query += " WHERE ts >= ?"
        params = (cutoff,)
    query += " ORDER BY ts"

    rows = conn.execute(query, params).fetchall()
    conn.close()

    if not rows:
        print("No data in metrics DB", file=sys.stderr)
        sys.exit(1)

    return [dict(r) for r in rows]


def load_from_csv(csv_path):
    """Load metrics from a CSV file."""
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        return [{k: float(v) for k, v in row.items()} for row in reader]


def fmt_ts(ts):
    """Convert epoch to datetime string."""
    return datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")


def create_dashboard(data, out_path):
    """Create a multi-subplot Plotly HTML dashboard."""
    ts_labels = [fmt_ts(r["ts"]) for r in data]
    ts_raw = [r["ts"] for r in data]

    # Create 5-row subplot grid
    fig = make_subplots(
        rows=5, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.04,
        subplot_titles=(
            "Load Average (1-min)",
            "Memory Usage",
            "Workers vs Dynamic Max",
            "z.ai API Quota",
            "Kanban Task Counts",
        ),
        row_heights=[0.22, 0.22, 0.22, 0.17, 0.17],
    )

    # ─── Row 1: Load ──────────────────────────────────────
    fig.add_trace(
        go.Scatter(x=ts_raw, y=[r["load1"] for r in data],
                   name="Load 1m", line=dict(color="#4ec9b0", width=1.5),
                   hovertemplate="<b>%{customdata}</b><br>Load: %{y:.2f}<extra></extra>",
                   customdata=ts_labels),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(x=ts_raw, y=[r["load_per_core"] for r in data],
                   name="Load/core", line=dict(color="#dcdcaa", width=1, dash="dot"),
                   hovertemplate="Load/core: %{y:.2f}<extra></extra>"),
        row=1, col=1,
    )
    # Threshold lines for load
    nproc = 4  # i7-7600U
    fig.add_hline(y=nproc, line_dash="dash", line_color="#666", line_width=0.5,
                  annotation_text=f"nproc={nproc}", row=1, col=1)
    fig.add_hline(y=nproc * 2, line_dash="dot", line_color="#999", line_width=0.5,
                  annotation_text=f"WARN={nproc*2}", row=1, col=1)

    # ─── Row 2: Memory ────────────────────────────────────
    fig.add_trace(
        go.Scatter(x=ts_raw, y=[r["mem_pct"] for r in data],
                   name="RAM %", line=dict(color="#c586c0", width=1.5),
                   fill="tozeroy", fillcolor="rgba(197,134,192,0.1)",
                   hovertemplate="<b>%{customdata}</b><br>RAM: %{y:.0f}%<extra></extra>",
                   customdata=ts_labels),
        row=2, col=1,
    )
    fig.add_trace(
        go.Scatter(x=ts_raw, y=[r["mem_avail_mb"] / 10 for r in data],
                   name="Avail (÷10 MB)", line=dict(color="#569cd6", width=1, dash="dot"),
                   hovertemplate="Avail: %{customdata}MB<extra></extra>",
                   customdata=[f"{r['mem_avail_mb']:.0f}" for r in data]),
        row=2, col=1,
    )
    # Memory thresholds
    fig.add_hline(y=80, line_dash="dash", line_color="#e06c75", line_width=0.5,
                  annotation_text="TRIM 80%", row=2, col=1)
    fig.add_hline(y=90, line_dash="dash", line_color="#ff0000", line_width=0.5,
                  annotation_text="EMERGENCY 90%", row=2, col=1)

    # ─── Row 3: Workers vs Max ────────────────────────────
    fig.add_trace(
        go.Bar(x=ts_raw, y=[r["workers"] for r in data],
               name="Running", marker_color="#4ec9b0",
               hovertemplate="<b>%{customdata}</b><br>Workers: %{y}<extra></extra>",
               customdata=ts_labels),
        row=3, col=1,
    )
    fig.add_trace(
        go.Scatter(x=ts_raw, y=[r["max_concurrent"] for r in data],
                   name="Max (dynamic)", line=dict(color="#ce9178", width=2, dash="dash"),
                   hovertemplate="Max: %{y}<extra></extra>"),
        row=3, col=1,
    )

    # ─── Row 4: API Quota ─────────────────────────────────
    fig.add_trace(
        go.Scatter(x=ts_raw, y=[r["api_quota_pct"] for r in data],
                   name="z.ai (ours)", line=dict(color="#dcdcaa", width=1.5),
                   fill="tozeroy", fillcolor="rgba(220,220,170,0.1)",
                   hovertemplate="<b>%{customdata}</b><br>Ours: %{y:.0f}%<extra></extra>",
                   customdata=ts_labels),
        row=4, col=1,
    )
    # Friend's key on same subplot (may be absent in old rows → default 0)
    friend_pct = [r.get("api_quota_friend_pct", 0) or 0 for r in data]
    fig.add_trace(
        go.Scatter(x=ts_raw, y=friend_pct,
                   name="z.ai (friend)", line=dict(color="#e06c75", width=1.5),
                   hovertemplate="<b>%{customdata}</b><br>Friend: %{y:.0f}%<extra></extra>",
                   customdata=ts_labels),
        row=4, col=1,
    )
    fig.add_hline(y=85, line_dash="dash", line_color="#e06c75", line_width=0.5,
                  annotation_text="BLOCK 85%", row=4, col=1)

    # ─── Row 5: Kanban Tasks ──────────────────────────────
    fig.add_trace(
        go.Scatter(x=ts_raw, y=[r["tasks_running"] for r in data],
                   name="Running", line=dict(color="#4ec9b0", width=1.5),
                   stackgroup="tasks"),
        row=5, col=1,
    )
    fig.add_trace(
        go.Scatter(x=ts_raw, y=[r["tasks_ready"] for r in data],
                   name="Ready", line=dict(color="#569cd6", width=1.5),
                   stackgroup="tasks"),
        row=5, col=1,
    )
    fig.add_trace(
        go.Scatter(x=ts_raw, y=[r["tasks_blocked"] for r in data],
                   name="Blocked", line=dict(color="#e06c75", width=1.5),
                   stackgroup="tasks"),
        row=5, col=1,
    )

    # ─── Layout ───────────────────────────────────────────
    title_range = ""
    if data:
        first = fmt_ts(data[0]["ts"])
        last = fmt_ts(data[-1]["ts"])
        title_range = f" ({first} → {last})"

    fig.update_layout(
        title=dict(
            text=f"Worker Management Dashboard{title_range}",
            font=dict(size=18, color="#cccccc"),
        ),
        template="plotly_dark",
        paper_bgcolor="#1e1e1e",
        plot_bgcolor="#1e1e1e",
        font=dict(color="#cccccc", size=11),
        height=1400,
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=60, r=30, t=80, b=40),
    )

    # X-axis formatting on bottom subplot only
    fig.update_xaxes(tickformat="%H:%M", nticks=12, row=5, col=1)
    for i in range(1, 5):
        fig.update_xaxes(showticklabels=False, row=i, col=1)

    # Y-axis labels
    fig.update_yaxes(title_text="Load", row=1, col=1)
    fig.update_yaxes(title_text="RAM %", row=2, col=1)
    fig.update_yaxes(title_text="Workers", row=3, col=1)
    fig.update_yaxes(title_text="API %", row=4, col=1)
    fig.update_yaxes(title_text="Tasks", row=5, col=1)

    # Save
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(str(out_path), include_plotlyjs=True)
    print(f"Dashboard saved: {out_path}")
    print(f"Data points: {len(data)}")
    return out_path


def main():
    parser = argparse.ArgumentParser(description="Plot worker metrics")
    parser.add_argument("--hours", type=int, default=None,
                        help="Only show last N hours")
    parser.add_argument("--out", type=str, default=None,
                        help="Output HTML path (default: ~/reports/worker-dashboard.html)")
    parser.add_argument("--csv", type=str, default=None,
                        help="Load from CSV instead of DB")
    args = parser.parse_args()

    if args.csv:
        data = load_from_csv(args.csv)
    else:
        data = load_from_db(hours=args.hours)

    out = Path(args.out) if args.out else DEFAULT_OUT
    create_dashboard(data, out)


if __name__ == "__main__":
    main()
