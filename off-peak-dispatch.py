#!/usr/bin/env python3
"""
Off-peak batch dispatcher — fires at 10:00 UTC when GLM-5.2 quota drops to 1x.
Assigns idle workers to unassigned ready tasks, then dispatches all boards.
Priority order: admin (manager fixes) > plebeian (CI/e2e) > tollgate (firmware).
"""
import subprocess, json, sys, time, re

def run(cmd, timeout=30):
    """Run a shell command and return stdout+stderr."""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        out = r.stdout
        if r.stderr:
            out += "\n" + r.stderr if out else r.stderr
        return out.strip()
    except subprocess.TimeoutExpired:
        return f"TIMEOUT after {timeout}s"
    except Exception as e:
        return f"ERROR: {e}"

def get_idle_workers(board):
    """Parse assignees output to find idle worker profiles."""
    out = run(f"hermes kanban --board {board} assignees")
    idle = []
    for line in out.split('\n'):
        line = line.strip()
        if '(idle)' in line:
            # Extract profile name (first column)
            parts = line.split()
            if parts and parts[0] != 'NAME':
                idle.append(parts[0])
    return idle

def get_ready_tasks(board):
    """Get list of ready task IDs from ls output."""
    out = run(f"hermes kanban --board {board} ls")
    ready = []
    for line in out.split('\n'):
        if '▶' in line and '(unassigned)' in line:
            # Extract task ID
            match = re.search(r'(t_[a-f0-9]+)', line)
            if match:
                ready.append(match.group(1))
    return ready

def get_blocked_tasks(board):
    """Get list of blocked task IDs from ls output."""
    out = run(f"hermes kanban --board {board} ls")
    blocked = []
    for line in out.split('\n'):
        if '⊘' in line:
            match = re.search(r'(t_[a-f0-9]+)', line)
            if match:
                blocked.append(match.group(1))
    return blocked

# ============================================================
# MAIN DISPATCH
# ============================================================
print(f"=== OFF-PEAK DISPATCH START: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())} ===")
print()

# Priority order
BOARDS = [
    ('admin', [
        # P0: Manager fixes
        ('t_40eb7f0c', 'M1: Restrict manager toolsets', 1),
        ('t_8647a2d0', 'M2: Set max_turns to 25', 1),
        ('t_0fafa5b5', 'W5: File Hermes feature requests', 2),
        # P1: Infrastructure
        ('t_85cd8f6a', 'B1: ensure-worktree.sh --sync mode', 2),
        ('t_e4185c90', 'B6: Python venv via uv', 2),
        ('t_48a209d7', 'Ansible role for state sync', 3),
        ('t_0fd13a15', 'Bootstrap script for new machine', 3),
        ('t_bea6ff70', 'FEAT: SQLite usage logging', 3),
        # P2: Ready unassigned
        ('t_a1ae2e19', 'B2: Worktree lock file', 3),
        ('t_54a23820', 'B5-v0.1: Unified dispatch wrapper', 3),
        ('t_715ff060', 'B5-v0.2: Dispatch wrapper lock integration', 4),
        ('t_e1c3aae8', 'B4: Force-push ban + context template', 4),
        ('t_36bbf4d0', 'C: File Hermes feature requests', 4),
        ('t_f294611e', 'FEAT: z.ai usage query', 4),
        ('t_c0f74d12', 'Install applesauce SDK skill', 5),
        ('t_0a2dfe03', 'GitHub↔ngit dual-posting', 5),
    ]),
    ('plebeian', [
        # P1: CI fixes
        ('t_98f13974', 'Fix CI failures on PR #1071', 1),
        ('t_a8834ab9', 'Fix prettier CI failure on PR #1069', 1),
        ('t_9fceeb27', 'Fix prettier CI failure on PR #1073', 1),
        ('t_6ca98a23', 'Investigate missing CI on PR #1066', 1),
        ('t_bbfae815', 'Fix e2e commerce selectors (#1071)', 1),
        ('t_be0b7bea', 'Add relay failover test coverage (#1072)', 1),
        # P2: e2e reliability
        ('t_3acaee6d', 'Run full e2e isolation experiment', 2),
        ('t_6c79e696', 'Fix networkidle across all e2e specs', 2),
        ('t_5eb02641', 'Fix auth.spec.ts stored-key login', 2),
        ('t_95a8d50b', 'A/B validate e2e fixes', 3),
        ('t_41485d0b', 'A/B validate auth+cart+pii+payments', 3),
        ('t_72cf27cf', 'Open PR for e2e reliability fixes', 3),
        # P3: Housekeeping
        ('t_dd6a6c1b', 'Rebase Wave 0 I/O seam', 4),
        ('t_3a745cea', 'Close stale fork PRs', 4),
        ('t_48885435', 'Create reviewer guide', 4),
        ('t_3c27d3d9', 'Retarget #1069 scraper to master', 4),
        ('t_30fe701f', 'Rebase #1068 Wave A1b', 5),
        ('t_93ef4a95', 'Run e2e isolation experiment', 5),
        ('t_5125f072', 'Monitor CI on #1071 and #1075', 5),
        ('t_0873585e', 'Post progress update on #1057', 5),
    ]),
    ('tollgate', [
        # P1: Blocked firmware
        ('t_61ad70bc', 'P1.2 ESP32 RX test firmware', 1),
        ('t_533447ec', 'P1.4 Python test runner', 1),
        # P1: Ready
        ('t_9fbe0b3e', 'Fix test-automation repo', 1),
        # P2: Phase 1 continuation
        ('t_c7396eea', 'P1.5 Run Phase 1 test matrix', 2),
        ('t_9d749517', 'P1.6 Write Phase 1 results report', 2),
        ('t_5ef62bb8', 'P2.0 Configure FLRC mode', 3),
        # P3: Phase 2/3/4
        ('t_378e81bd', 'P2.1 RP2040 bidirectional firmware', 4),
        ('t_f8f85038', 'P2.2 Build speed benchmark suite', 4),
        ('t_9e842b4a', 'P2.3 Implement PIO+DMA SPI', 4),
        ('t_5e47d330', 'P3.0 Design UART command protocol', 5),
        ('t_7b5c0d1e', 'P3.1 RP2040 coprocessor firmware', 5),
        ('t_9bcf4839', 'P3.2 ESP32 GPS parser module', 5),
        ('t_17e83cd7', 'P3.3 ESP32 UART radio interface', 5),
        ('t_fd2a2d14', 'P3.4 Telemetry formatter', 5),
        ('t_12137c1d', 'P3.5 Ground station receiver', 5),
        ('t_46663026', 'P3.6 End-to-end mission test', 5),
        ('t_22d41c82', 'P4.0 Design PIO state machine', 5),
        ('t_4b8103d4', 'P4.1 DMA chaining zero-copy', 5),
    ]),
]

for board_name, tasks in BOARDS:
    print(f"\n{'='*60}")
    print(f"BOARD: {board_name}")
    print(f"{'='*60}")

    # 1. Try to unblock blocked tasks (may already be unblocked)
    blocked = get_blocked_tasks(board_name)
    if blocked:
        print(f"\n--- Attempting to unblock {len(blocked)} blocked tasks ---")
        ids = ' '.join(blocked)
        result = run(f"hermes kanban --board {board_name} unblock {ids}")
        print(result)

    # 2. Force-promote tasks with unsatisfied parent deps (M2, P1.5, etc.)
    #    Only for tasks we know have false dependencies
    force_promote = {
        'admin': ['t_8647a2d0'],  # M2 depends on M1, but M1 is running
        'tollgate': ['t_c7396eea'],  # P1.5 depends on CTX card
    }
    if board_name in force_promote:
        for tid in force_promote[board_name]:
            result = run(f"hermes kanban --board {board_name} promote {tid} --force")
            if result:
                print(f"\n--- Force-promote {tid}: {result} ---")

    # 3. Get idle workers
    idle_workers = get_idle_workers(board_name)
    print(f"\n--- Idle workers: {len(idle_workers)} ---")
    if idle_workers:
        print(f"  First 10: {', '.join(idle_workers[:10])}")

    # 4. Get unassigned ready tasks
    unassigned_ready = get_ready_tasks(board_name)
    print(f"\n--- Unassigned ready tasks: {len(unassigned_ready)} ---")

    # 5. Assign unassigned tasks to idle workers (round-robin)
    assigned_count = 0
    for i, task_id in enumerate(unassigned_ready):
        if not idle_workers:
            print(f"  ⚠️ No idle workers left for {task_id}")
            break
        worker = idle_workers[i % len(idle_workers)]
        result = run(f"hermes kanban --board {board_name} assign {task_id} {worker}")
        if 'error' in result.lower() or 'cannot' in result.lower():
            print(f"  ❌ {task_id} → {worker}: {result}")
        else:
            print(f"  ✅ {task_id} → {worker}")
            assigned_count += 1

    print(f"\n--- Assigned {assigned_count}/{len(unassigned_ready)} tasks ---")

    # 6. Dispatch
    print(f"\n--- Dispatching {board_name} ---")
    result = run(f"hermes kanban --board {board_name} dispatch --failure-limit 5")
    print(result)

    # Small delay between boards
    time.sleep(3)

# 7. Final stats
print(f"\n{'='*60}")
print("FINAL BOARD STATS")
print(f"{'='*60}")
for board_name, _ in BOARDS:
    print(f"\n--- {board_name} ---")
    print(run(f"hermes kanban --board {board_name} stats"))

print(f"\n=== DISPATCH COMPLETE: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())} ===")
