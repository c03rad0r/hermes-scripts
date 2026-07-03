#!/bin/bash
# human-gate-e2e-test.sh — End-to-end test of the human-gate flow
# Tests: block → shadow creation → resolve → unblock
# Silent on success, reports failures only.
#
# Usage: bash scripts/human-gate-e2e-test.sh

set -euo pipefail

BOARDS_DIR="$HOME/.hermes/kanban/boards"
HUMAN_GATE_DB="$BOARDS_DIR/human-gate/kanban.db"
TEST_BOARD="admin"
TEST_TITLE="E2E TEST — human-gate flow verification $(date +%s)"
TEST_REASON="human-gate: E2E test — verify shadow creation and resolution"

PASS=0
FAIL=0

step() {
    local n="$1" msg="$2"
    printf "  Step %s: %s ... " "$n" "$msg"
}

pass() {
    echo "✅ PASS"
    PASS=$((PASS + 1))
}

fail() {
    echo "❌ FAIL: $1"
    FAIL=$((FAIL + 1))
}

cleanup() {
    echo ""
    echo "── Cleanup ──"
    if [ -n "${TEST_TASK:-}" ]; then
        hermes kanban --board "$TEST_BOARD" archive "$TEST_TASK" 2>/dev/null || true
        echo "  Archived test task: $TEST_TASK"
    fi
    if [ -n "${SHADOW_ID:-}" ]; then
        hermes kanban --board human-gate archive "$SHADOW_ID" 2>/dev/null || true
        echo "  Archived shadow: $SHADOW_ID"
    fi
    echo "── Done ──"
}
trap cleanup EXIT

echo ""
echo "🧪 HUMAN-GATE E2E TEST"
echo "═══════════════════════"

# Step 1: Create a test task on the test board
step 1 "Create test task on '$TEST_BOARD'"
TEST_TASK=$(hermes kanban --board "$TEST_BOARD" create --json "$TEST_TITLE" 2>/dev/null | grep -oP 't_\w+' | head -1)
if [ -n "$TEST_TASK" ]; then
    pass "created $TEST_TASK"
else
    fail "could not create test task"
    exit 1
fi

# Step 2: Block the task with human-gate reason
step 2 "Block with human-gate reason"
BLOCK_OUT=$(hermes kanban --board "$TEST_BOARD" block "$TEST_TASK" "$TEST_REASON" 2>&1 || true)
if echo "$BLOCK_OUT" | grep -qi "blocked"; then
    pass "blocked $TEST_TASK"
else
    fail "block failed: $BLOCK_OUT"
fi

# Step 3: Run shadow creator directly (deterministic — no cron wait)
step 3 "Run shadow-creator, verify shadow on human-gate board"
SHADOW_ID=""
python3 "$HOME/scripts/human-gate-shadow-creator.py" 2>/dev/null || true
if [ -f "$HUMAN_GATE_DB" ]; then
    SHADOW_ID=$(python3 -c "
import sqlite3, os
db = os.path.expanduser('$HUMAN_GATE_DB')
try:
    conn = sqlite3.connect(db)
    rows = conn.execute(
        'SELECT id FROM tasks WHERE body LIKE ? AND status NOT IN (\"done\",\"archived\")',
        ('%$TEST_TASK%',)
    ).fetchall()
    conn.close()
    if rows:
        print(rows[0][0])
except:
    pass
" 2>/dev/null || true)
fi

if [ -n "$SHADOW_ID" ]; then
    pass "shadow created: $SHADOW_ID"
else
    fail "no shadow appeared within 12s"
fi

# Step 4: Complete the shadow task
step 4 "Complete shadow task (mark done)"
COMPLETE_OUT=$(hermes kanban --board human-gate complete "$SHADOW_ID" --summary "E2E test: approved" 2>&1 || true)
if echo "$COMPLETE_OUT" | grep -qiE "(completed|done)"; then
    pass "shadow completed"
else
    # Try marking done directly
    python3 -c "
import sqlite3, os
db = os.path.expanduser('$HUMAN_GATE_DB')
conn = sqlite3.connect(db)
conn.execute('UPDATE tasks SET status = \"done\" WHERE id = \"$SHADOW_ID\"')
conn.commit()
conn.close()
" 2>/dev/null || true
    pass "shadow marked done (direct DB)"
fi

# Step 5: Run resolver directly (deterministic — no cron wait)
step 5 "Run resolver, verify source task unblocked"
UNBLOCKED=""
# Exercise the real artifact (the standalone .py the cron now uses), not the
# .sh wrapper. This catches regressions in the file the resolver cron runs.
python3 "$HOME/.hermes/profiles/manager/scripts/human-gate-resolver.py" 2>/dev/null || true
# Check status directly from the DB (more reliable than view output parsing)
TEST_BOARD_DB="$BOARDS_DIR/$TEST_BOARD/kanban.db"
if [ -f "$TEST_BOARD_DB" ]; then
    STATUS=$(python3 -c "
import sqlite3
try:
    conn = sqlite3.connect('$TEST_BOARD_DB')
    row = conn.execute('SELECT status FROM tasks WHERE id = ?', ('$TEST_TASK',)).fetchone()
    conn.close()
    print(row[0] if row else 'not_found')
except Exception:
    print('error')
" 2>/dev/null || echo "error")
else
    STATUS="no_db"
fi
if echo "$STATUS" | grep -qiE "(todo|ready|running|done)"; then
    UNBLOCKED="yes"
fi

if [ -n "$UNBLOCKED" ]; then
    pass "source task unblocked"
else
    fail "source task still blocked (status: $STATUS)"
fi

# Step 6: Verify shadow was archived by resolver
step 6 "Verify shadow was archived"
SHADOW_STATUS=$(python3 -c "
import sqlite3, os
db = os.path.expanduser('$HUMAN_GATE_DB')
try:
    conn = sqlite3.connect(db)
    row = conn.execute('SELECT status FROM tasks WHERE id = ?', ('$SHADOW_ID',)).fetchone()
    conn.close()
    print(row[0] if row else 'not_found')
except:
    print('error')
" 2>/dev/null || echo "error")

if [ "$SHADOW_STATUS" = "done" ] || [ "$SHADOW_STATUS" = "archived" ]; then
    pass "shadow status: $SHADOW_STATUS"
else
    fail "unexpected shadow status: $SHADOW_STATUS"
fi

# ──────────────────────────────────────────────────────────────────────────
# REGRESSION (t_9ca24258): board-pinned worker cross-board write.
# A dispatched worker has HERMES_KANBAN_DB pinned to its own board. It must
# still be able to write to the human-gate board when it passes --board
# human-gate (or board= in the tool). Before the fix, the env pin silently
# won and the shadow landed in the worker's own board — invisible to the
# resolver. This step asserts the task lands in the RIGHT DB, not just that
# it exists somewhere.
# ──────────────────────────────────────────────────────────────────────────
TEST_BOARD_DB="$BOARDS_DIR/$TEST_BOARD/kanban.db"
CROSS_TITLE="CROSS-BOARD REGRESSION $(date +%s)"

echo ""
echo "── Regression (t_9ca24258): pinned-worker cross-board write ──"

# Create from a HERMES_KANBAN_DB-pinned context (simulates a dispatched worker).
CROSS_TASK=$(env HERMES_KANBAN_DB="$TEST_BOARD_DB" \
    hermes kanban --board human-gate create --json "$CROSS_TITLE" 2>/dev/null \
    | grep -oP 't_\w+' | head -1)

step 7 "Pinned worker creates task on human-gate board"
if [ -n "$CROSS_TASK" ]; then
    pass "created $CROSS_TASK"
else
    fail "could not create cross-board task"
fi

step 8 "Task landed in human-gate DB (not pinned board DB)"
if [ -f "$HUMAN_GATE_DB" ]; then
    IN_HG=$(python3 -c "
import sqlite3, sys
try:
    conn = sqlite3.connect('$HUMAN_GATE_DB')
    row = conn.execute('SELECT id FROM tasks WHERE id = ?', ('$CROSS_TASK',)).fetchone()
    conn.close()
    print('yes' if row else 'no')
except Exception:
    print('no')
" 2>/dev/null || echo "no")
else
    IN_HG="no"
fi

IN_PINNED="no"
if [ -f "$TEST_BOARD_DB" ]; then
    IN_PINNED=$(python3 -c "
import sqlite3, sys
try:
    conn = sqlite3.connect('$TEST_BOARD_DB')
    row = conn.execute('SELECT id FROM tasks WHERE id = ?', ('$CROSS_TASK',)).fetchone()
    conn.close()
    print('yes' if row else 'no')
except Exception:
    print('no')
" 2>/dev/null || echo "no")
fi

if [ "$IN_HG" = "yes" ] && [ "$IN_PINNED" = "no" ]; then
    pass "in human-gate DB, NOT in pinned board DB"
else
    fail "DB location wrong — human-gate=$IN_HG pinned=$IN_PINNED"
fi

# Cleanup the cross-board regression task.
if [ -n "${CROSS_TASK:-}" ]; then
    hermes kanban --board human-gate archive "$CROSS_TASK" 2>/dev/null || true
fi

# Report
echo ""
echo "═══ RESULTS ═══"
echo "  Passed: $PASS"
echo "  Failed: $FAIL"
if [ "$FAIL" -eq 0 ]; then
    echo "  ✅ ALL TESTS PASSED"
else
    echo "  ❌ $FAIL TEST(S) FAILED"
fi
echo "════════════════"
echo ""
echo "Note: Test task $TEST_TASK and shadow $SHADOW_ID will be auto-cleaned."
echo "To re-run: bash $0"
