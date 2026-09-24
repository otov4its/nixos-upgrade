#!/usr/bin/env bash

set -o errexit
set -o nounset
set -o pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly PROJECT_ROOT
readonly BIN_FILE="$PROJECT_ROOT/src/bin/nixos-upgrade"

script=$(<"$BIN_FILE")
check_block=$(sed -n '/^function check_singleton {$/,/^}$/p' "$BIN_FILE")

if [[ "$script" != *'XDG_RUNTIME_DIR'* ]]; then
  printf 'launcher must use XDG_RUNTIME_DIR for the singleton lock\n' >&2
  exit 1
fi

if [[ "$script" != *'nixos-upgrade.lock'* ]]; then
  printf 'launcher must use a stable lock filename\n' >&2
  exit 1
fi

worker_reference="\"\$WORKER\""
if [[ "$check_block" == *"$worker_reference"* ]]; then
  printf 'singleton lock must not use the versioned worker path\n' >&2
  exit 1
fi

lock_redirect=">>\"\$lock_file\""
if [[ "$check_block" != *"$lock_redirect"* ]]; then
  printf 'singleton lock must be created at runtime\n' >&2
  exit 1
fi

TEST_ROOT=$(mktemp --directory /tmp/nixos-upgrade-lock-test.XXXXXXXXXX)
readonly TEST_ROOT
readonly RUNTIME_DIR="$TEST_ROOT/runtime"
readonly READY_FILE="$TEST_ROOT/ready"
readonly RUNNER="$TEST_ROOT/runner"
first_pid=""
trap 'kill "$first_pid" 2>/dev/null || true; wait "$first_pid" 2>/dev/null || true; rm --recursive --force "$TEST_ROOT"' EXIT
mkdir --parents "$RUNTIME_DIR"

{
  cat <<'RUNNER_HEADER'
#!/usr/bin/env bash
set -o errexit
set -o nounset
set -o pipefail

log_error() {
  printf '%s\n' "$*" >&2
}
RUNNER_HEADER
  printf '%s\n' "$check_block"
  cat <<RUNNER_BODY

check_singleton
touch "$READY_FILE"
sleep 2
RUNNER_BODY
} > "$RUNNER"
chmod +x "$RUNNER"

XDG_RUNTIME_DIR="$RUNTIME_DIR" "$RUNNER" &
first_pid=$!
for _ in {1..100}; do
  if [[ -e "$READY_FILE" ]]; then
    break
  fi
  sleep 0.01
done
test -e "$READY_FILE"

if XDG_RUNTIME_DIR="$RUNTIME_DIR" "$RUNNER" 2>"$TEST_ROOT/second-error"; then
  printf 'second launcher acquired an already-held singleton lock\n' >&2
  exit 1
fi

grep -q 'process is already running' "$TEST_ROOT/second-error"
wait "$first_pid"
first_pid=""
test -f "$RUNTIME_DIR/nixos-upgrade.lock"

printf 'ok: singleton lock is per-user and independent of worker version\n'
