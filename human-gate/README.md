# human-gate automation scripts

Auto-flow for the human-gate escalation queue: when a worker blocks a task with a
`human-gate:` reason, a shadow appears on the human-gate board; when the human
marks it done, the original is unblocked and the shadow archived.

## Files

| File | Purpose |
|---|---|
| `human-gate-shadow-creator.py` | Scan all boards for blocked human-gate tasks; create shadows. |
| `human-gate-resolver.py` | Scan done shadows; unblock originals + archive shadows. |
| `human-gate-resolver.sh` | Thin wrapper → `human-gate-resolver.py` (backwards compat). |
| `human-gate-digest-v2.py` | ICS Liaison digest; silent when healthy. |
| `human-gate-e2e-test.sh` | End-to-end harness (block→shadow→resolve→unblock). |
| `human-gate-mcp.py` | MCP server (request_review/merge/approval, list, complete). |

## Deployment layout

These scripts are version-controlled HERE for state replication / disaster
recovery. At runtime, Hermes crons resolve scripts relative to the profile
scripts dir, so the LIVE copies must also exist at:

    ~/.hermes/profiles/manager/scripts/<name>

Canonical working copies live at `~/scripts/<name>`. After editing here or in
`~/scripts/`, mirror to `~/.hermes/profiles/manager/scripts/`.

## Scheduled crons (hermes cron)

| Name | Schedule | Script | Mode |
|---|---|---|---|
| human-gate-shadow-creator | every 2m | human-gate-shadow-creator.py | no-agent |
| human-gate-resolver | every 2m | human-gate-resolver.py | no-agent |
| human-gate-digest | every 4h | human-gate-digest-v2.py | no-agent, deliver=origin |
| nostr-kanban-sync | every 15m | nostr-kanban-sync.sh | no-agent |
| nostr-kanban-inbound | every 15m | nostr-kanban-inbound-sync.sh | no-agent |

## Design notes

- `resolver.py` is a STANDALONE scan file (no inline `python3 -c`). The previous
  `resolver.sh` embedded Python via `python3 -c "..."` which (a) is blocked by
  agent command-approval layers and (b) has brittle bash/Python quoting.
- Both scan scripts are SILENT when there is nothing to report (anomaly-only).
- Body formats accepted by the resolver: JSON `{"source_board":"fips"}` and
  human-readable `Source board: fips`.
