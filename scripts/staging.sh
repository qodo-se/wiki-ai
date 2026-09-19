#!/usr/bin/env bash
set -euo pipefail

# Isolated "staging" copy of the stack (separate volumes + port) so it can
# run alongside your normal dev stack. Uses whatever's checked out here —
# `git checkout <branch>` first if you want to stage something specific.
#
# Usage:
#   scripts/staging.sh up      start staging on http://localhost:8082
#   scripts/staging.sh down    stop it (add -v to also wipe its volumes)

PORT="${STAGING_PORT:-8082}"

case "${1:-}" in
  up)   API_PORT="$PORT" docker compose -p wiki-staging up -d --build ;;
  down) docker compose -p wiki-staging down ${2:+"$2"} ;;
  *)    echo "Usage: $0 {up|down}" >&2; exit 1 ;;
esac
