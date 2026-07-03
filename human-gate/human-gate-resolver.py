#!/usr/bin/env python3
"""
human-gate-resolver.py — Scan completed human-gate shadows, unblock originals.

Companion to human-gate-shadow-creator.py. When a human marks a shadow task
"done" on the human-gate board, this resolver:
  1. Reads source_board / source_task from the shadow body
     (accepts JSON `\"source_board\": \"fips\"` or human-readable `Source board: fips`)
  2. Unblocks the original task on its board (via `hermes kanban --board ... unblock`)
  3. Archives the shadow so it isn't reprocessed

Silent when nothing to resolve. Prints resolved items only.
No inline `python3 -c` — this IS the standalone scan file (infra-doc pitfall #2 fix).

Run as: hermes cron --no-agent --script scripts/human-gate-resolver.py
"""
import os
import re
import subprocess
import sqlite3
import sys

HOME = os.path.expanduser("~")
BOARDS_DIR = os.environ.get(
    "HERMES_KANBAN_BOARDS_DIR",
    os.path.join(HOME, ".hermes", "kanban", "boards"),
)
HUMAN_GATE_DB = os.path.join(BOARDS_DIR, "human-gate", "kanban.db")

# Match both formats:
#   "source_board": "fips"        (JSON, escaped or not)
#   Source board: fips            (human-readable, space, Title case)
BOARD_RE = re.compile(r'[Ss]ource[_\s][Bb]oard[":\s]+([\w-]+)')
TASK_RE = re.compile(r'[Ss]ource[_\s][Tt]ask[":\s]+([\w-]+)')


def run_kanban(board: str, verb: str, task_id: str, timeout: int = 15) -> bool:
    """Invoke the hermes kanban CLI. Returns True on success."""
    try:
        r = subprocess.run(
            ["hermes", "kanban", "--board", board, verb, task_id],
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode == 0
    except (subprocess.SubprocessError, FileNotFoundError) as e:
        print(f"ERROR: kanban {verb} {board}/{task_id} failed: {e}", file=sys.stderr)
        return False


def resolve_completed_shadows() -> list[str]:
    """Find done shadows with source info, unblock originals, archive shadows."""
    resolved: list[str] = []
    if not os.path.exists(HUMAN_GATE_DB):
        return resolved

    try:
        conn = sqlite3.connect(HUMAN_GATE_DB)
        rows = conn.execute(
            "SELECT id, title, body FROM tasks WHERE status = 'done'"
        ).fetchall()
        conn.close()
    except sqlite3.Error as e:
        print(f"ERROR reading human-gate board: {e}", file=sys.stderr)
        return resolved

    for tid, title, body in rows:
        body_lower = (body or "").lower()
        has_source = "source_board" in body_lower or "source board" in body_lower
        if not (body and has_source):
            continue

        board_match = BOARD_RE.search(body)
        task_match = TASK_RE.search(body)
        if not (board_match and task_match):
            continue

        src_board = board_match.group(1)
        src_task = task_match.group(1)

        ok = run_kanban(src_board, "unblock", src_task)
        if ok:
            resolved.append(f"{src_board}/{src_task} \u2190 human-gate/{tid}")
        else:
            print(
                f"WARN: unblock {src_board}/{src_task} failed; "
                f"archiving shadow {tid} anyway",
                file=sys.stderr,
            )

        # Archive the shadow regardless so it isn't reprocessed every tick.
        # (A failed unblock usually means the source was already resolved/
        #  archived independently — the human-side action is still complete.)
        run_kanban("human-gate", "archive", tid)

    return resolved


def main() -> None:
    resolved = resolve_completed_shadows()
    for r in resolved:
        print(f"\u2705 Resolved: {r}")


if __name__ == "__main__":
    main()
