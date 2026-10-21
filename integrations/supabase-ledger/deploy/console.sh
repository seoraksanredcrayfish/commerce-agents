#!/usr/bin/env bash
# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0
#
# Run the merchant console over the ledger, with the environment in .env beside this file.
# The console asks y/N before any staged change applies.

set -euo pipefail

PREFIX="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
set -a
# shellcheck disable=SC1091
. "$PREFIX/.env"
set +a

for name in LEDGER_DSN LEDGER_TENANT_ID; do
  [ -n "${!name:-}" ] || { echo "console: $name is empty in $PREFIX/.env" >&2; exit 1; }
done
# Either credential runs the console; an empty one is unset so it cannot shadow the other.
for name in ANTHROPIC_API_KEY CLAUDE_CODE_OAUTH_TOKEN; do
  [ -n "${!name:-}" ] || unset "$name"
done
[ -n "${ANTHROPIC_API_KEY:-}${CLAUDE_CODE_OAUTH_TOKEN:-}" ] \
  || { echo "console: set ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN in $PREFIX/.env" >&2; exit 1; }

exec "$PREFIX/venv/bin/python" "$PREFIX/src/merchant-agent/runtime-agent-sdk/main.py" \
  --backend ledger_backend.deployment:build_merchant "$@"
