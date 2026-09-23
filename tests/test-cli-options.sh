#!/usr/bin/env bash

set -o errexit
set -o nounset
set -o pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly PROJECT_ROOT
readonly PYTHON_FILE="$PROJECT_ROOT/src/lib/nixos-upgrade.py"
TEST_ROOT="$(mktemp --directory /tmp/nixos-upgrade-cli-test.XXXXXXXXXX)"
readonly TEST_ROOT
readonly STDERR_FILE="$TEST_ROOT/stderr"

cleanup() {
  rm --recursive --force "$TEST_ROOT"
}
trap cleanup EXIT

exec 3<>/dev/null
exec 4<>/dev/null
exec 5<>/dev/null

set +o errexit
NAME=nixos-upgrade-test \
CMD_IFS=: \
PY_SH_FD=3 \
SH_PY_FD=4 \
COMMIT_MSG_W_FD=5 \
TMP_DIR="$TEST_ROOT" \
TERM_CORE_SIGS="" \
python3 "$PYTHON_FILE" --color=never --assume-yes --assume-no \
  2>"$STDERR_FILE"
status=$?
set -o errexit

test "$status" -eq 64
grep -Fq "not allowed with argument" "$STDERR_FILE"

printf 'ok: conflicting assume-yes/assume-no options are rejected\n'
