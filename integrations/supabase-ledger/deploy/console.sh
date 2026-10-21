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

for name in ANTHROPIC_API_KEY LEDGER_DSN LEDGER_TENANT_ID; do
  [ -n "${!name:-}" ] || { echo "console: $name is empty in $PREFIX/.env" >&2; exit 1; }
done

exec "$PREFIX/venv/bin/python" "$PREFIX/src/merchant-agent/runtime-agent-sdk/main.py" \
  --backend ledger_backend.deployment:build_merchant "$@"
