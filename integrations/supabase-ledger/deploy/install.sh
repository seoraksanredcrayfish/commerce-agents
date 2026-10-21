#!/usr/bin/env bash
# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0
#
# Install the ledger merchant console on a Linux host, under one directory and nothing else:
#
#   sudo bash install.sh [git-ref]        # default ref: main
#
# Writes only $PREFIX (default /opt/commerce-agent): src/ (this repository at the ref),
# venv/, .env (from the template, mode 600, kept if present), and console.sh. It creates
# no user, no service, and no cron, installs no system package, and changes no file
# outside $PREFIX. It stops when Python 3.11+ or git is missing instead of installing them.
# Running it again updates src/ to the ref and reinstalls the packages; .env is kept.

set -euo pipefail

PREFIX="${PREFIX:-/opt/commerce-agent}"
REPO="${REPO:-https://github.com/seoraksanredcrayfish/commerce-agents.git}"
REF="${1:-main}"
DEPLOY="$PREFIX/src/integrations/supabase-ledger/deploy"

fail() { echo "install: $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "run as root (writes $PREFIX)"
command -v git >/dev/null || fail "git is missing; install it first"
PYTHON="${PYTHON:-$(command -v python3.13 || command -v python3.12 || command -v python3.11 || command -v python3 || true)}"
[ -n "$PYTHON" ] || fail "python3 is missing; install Python 3.11 or newer first"
"$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 11))' \
  || fail "$PYTHON is $("$PYTHON" -V 2>&1); Python 3.11 or newer is required"
"$PYTHON" -c 'import venv, ensurepip' 2>/dev/null \
  || fail "$PYTHON has no venv/ensurepip (on Debian/Ubuntu: the python3.X-venv package)"

mkdir -p "$PREFIX"
chmod 700 "$PREFIX"

if [ -d "$PREFIX/src/.git" ]; then
  [ -z "$(git -C "$PREFIX/src" status --porcelain)" ] || fail "$PREFIX/src has local changes; leaving it"
  git -C "$PREFIX/src" fetch --quiet origin "$REF"
  git -C "$PREFIX/src" checkout --quiet --detach FETCH_HEAD
else
  git clone --quiet "$REPO" "$PREFIX/src"
  git -C "$PREFIX/src" fetch --quiet origin "$REF"
  git -C "$PREFIX/src" checkout --quiet --detach FETCH_HEAD
fi
echo "install: src at $(git -C "$PREFIX/src" rev-parse --short HEAD) ($REF)"

[ -x "$PREFIX/venv/bin/python" ] || "$PYTHON" -m venv "$PREFIX/venv"
(
  cd "$PREFIX/src"
  "$PREFIX/venv/bin/python" -m pip install --quiet --upgrade pip
  "$PREFIX/venv/bin/python" -m pip install --quiet -r requirements.txt
  "$PREFIX/venv/bin/python" -m pip install --quiet -e "./integrations/supabase-ledger[postgres]"
)
echo "install: packages installed in $PREFIX/venv"

if [ ! -f "$PREFIX/.env" ]; then
  install -m 600 "$DEPLOY/commerce-agent.env.example" "$PREFIX/.env"
  echo "install: wrote $PREFIX/.env from the template; fill it in before the first run"
else
  chmod 600 "$PREFIX/.env"
  echo "install: kept the existing $PREFIX/.env"
fi
install -m 700 "$DEPLOY/console.sh" "$PREFIX/console.sh"

cat <<EOF

Next:
  1. Fill in $PREFIX/.env (see the comments in it). Enter the API key without echoing it:
       read -rs KEY && sed -i "s|^ANTHROPIC_API_KEY=.*|ANTHROPIC_API_KEY=\$KEY|" $PREFIX/.env && unset KEY
  2. Start the console:
       $PREFIX/console.sh
EOF
