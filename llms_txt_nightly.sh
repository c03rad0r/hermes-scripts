#!/usr/bin/env bash
# Cron entry point (required to live in ~/.hermes/scripts/). Delegates to the
# canonical ansible-deployed watchdog in ~/.hermes/bot/ (single source of truth,
# kept in sync by the 00-bootstrap role) so the two never drift.
exec "${HOME:-/home/c03rad0r}/.hermes/bot/llms_txt_nightly.sh" "$@"
