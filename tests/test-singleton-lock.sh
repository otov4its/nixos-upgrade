#!/usr/bin/env bash

set -o errexit
set -o nounset
set -o pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly PROJECT_ROOT
readonly BIN_FILE="$PROJECT_ROOT/src/bin/nixos-upgrade"
readonly PYTHON_FILE="$PROJECT_ROOT/src/lib/nixos-upgrade.py"

if grep --extended-regexp --quiet 'parse_options|check_singleton|XDG_RUNTIME_DIR|flock|man --pager' \
  "$BIN_FILE"; then
  printf 'launcher must delegate CLI parsing and singleton locking to Python\n' >&2
  exit 1
fi

for python_contract in 'XDG_RUNTIME_DIR' 'nixos-upgrade.lock' 'fcntl.flock'; do
  if ! grep --fixed-strings --quiet -- "$python_contract" "$PYTHON_FILE"; then
    printf 'Python CLI is missing required lock behavior: %s\n' "$python_contract" >&2
    exit 1
  fi
done

PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s "$PROJECT_ROOT/tests" -p 'test_cli_startup.py' -v
