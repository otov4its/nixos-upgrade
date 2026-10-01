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
readonly STDOUT_FILE="$TEST_ROOT/stdout"

cleanup() {
  rm --recursive --force "$TEST_ROOT"
}
trap cleanup EXIT

set +o errexit
NAME=nixos-upgrade-test \
  TERM_CORE_SIGS="" \
  python3 "$PYTHON_FILE" --color=never --assume-yes --assume-no \
  2>"$STDERR_FILE"
status=$?
set -o errexit

test "$status" -eq 64
grep -Fq "not allowed with argument" "$STDERR_FILE"

printf 'ok: conflicting assume-yes/assume-no options are rejected\n'

set +o errexit
NAME=nixos-upgrade-test \
  TERM_CORE_SIGS="" \
  python3 "$PYTHON_FILE" --color=never --no-update-lock-file \
  --inputs nixpkgs 2>"$STDERR_FILE"
status=$?
set -o errexit

test "$status" -eq 64
grep -Fq "not allowed with argument" "$STDERR_FILE"
printf 'ok: --inputs conflicts with --no-update-lock-file\n'

for option in --help --version; do
  set +o errexit
  env -u VERSION -u XDG_RUNTIME_DIR -u TERM_CORE_SIGS \
    NAME=nixos-upgrade-test python3 "$PYTHON_FILE" "$option" \
    >"$STDOUT_FILE" 2>"$STDERR_FILE"
  status=$?
  set -o errexit

  test "$status" -eq 0
  if [ "$option" = "--help" ]; then
    grep -Fq "usage:" "$STDOUT_FILE"
  else
    grep -Fxq "development" "$STDOUT_FILE"
  fi
  printf 'ok: %s exits before runtime setup\n' "$option"
done
