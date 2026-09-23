#!/usr/bin/env bash

set -o errexit
set -o nounset
set -o pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly PROJECT_ROOT
readonly WORKER="$PROJECT_ROOT/src/lib/privileged-worker"
TEST_ROOT="$(mktemp --directory /tmp/nixos-upgrade-test.XXXXXXXXXX)"
readonly TEST_ROOT
readonly FLAKE_DIR="$TEST_ROOT/flake"
readonly STAGING_DIR="$TEST_ROOT/staging"
readonly FAKE_BIN="$TEST_ROOT/bin"

worker_pid=""
cmd_fd=""
reply_fd=""

cleanup() {
  if [[ -n "$worker_pid" ]]; then
    kill "$worker_pid" 2>/dev/null || true
    wait "$worker_pid" 2>/dev/null || true
  fi
  rm --recursive --force "$TEST_ROOT"
}
trap cleanup EXIT

mkdir --parents "$FLAKE_DIR/.git" "$STAGING_DIR" "$FAKE_BIN"
printf 'flake source\n' > "$FLAKE_DIR/flake.nix"
printf 'original lock\n' > "$FLAKE_DIR/flake.lock"
printf 'git metadata must survive\n' > "$FLAKE_DIR/.git/keep"

cat > "$FAKE_BIN/nix" <<'EOF'
#!/usr/bin/env bash
set -o errexit
set -o nounset
set -o pipefail

if [[ "$1" == "flake" && "$2" == "show" ]]; then
  test "$3" = "--no-write-lock-file"
  touch "$4/.worker-show"
  printf 'nixosConfigurations\n'
  exit 0
fi

if [[ "$1" == "flake" && "$2" == "update" ]]; then
  touch "$4/.worker-update"
  test "$6" = "$TMP_DIR/flake.lock"
  output_lock=""
  while (($# > 0)); do
    if [[ "$1" == "--output-lock-file" ]]; then
      output_lock="$2"
      break
    fi
    shift
  done
  printf 'updated lock\n' > "$output_lock"
  exit 0
fi

if [[ "$1" == "build" ]]; then
  config="${!#}"
  test "$2" = "--out-link"
  test "$3" = "$TMP_DIR/result"
  test "$4" = "--no-write-lock-file"
  test "$5" = "--reference-lock-file"
  test "$6" = "$TMP_DIR/flake.lock"
  touch "${config%%#*}/.worker-build"
  out_link=""
  for ((index = 1; index <= $#; index++)); do
    if [[ "${!index}" == "--out-link" ]]; then
      next=$((index + 1))
      out_link="${!next}"
      break
    fi
  done
  mkdir --parents "$TMP_DIR/closure"
  ln --symbolic "$TMP_DIR/closure" "$out_link"
  exit 0
fi

printf 'unexpected fake nix invocation\n' >&2
exit 1
EOF
chmod +x "$FAKE_BIN/nix"

cat > "$FAKE_BIN/git" <<'EOF'
#!/usr/bin/env bash
printf 'git must not be called by the worker\n' >&2
exit 1
EOF
chmod +x "$FAKE_BIN/git"

mkfifo "$TEST_ROOT/commands" "$TEST_ROOT/replies"

PATH="$FAKE_BIN:$PATH" \
NAME=nixos-upgrade-test \
CMD_IFS=: \
TMP_DIR="$STAGING_DIR" \
PY_SH_FD=3 \
SH_PY_FD=4 \
LOCK_FD=5 \
COMMIT_MSG_W_FD=6 \
COMMIT_MSG_R_FD=7 \
TERM_CORE_SIGS="" \
bash "$WORKER" 3<"$TEST_ROOT/commands" 4>"$TEST_ROOT/replies" &
worker_pid=$!

exec {cmd_fd}>"$TEST_ROOT/commands"
exec {reply_fd}<"$TEST_ROOT/replies"

send_command() {
  printf '%s\n' "$1" >&"$cmd_fd"
  local reply
  read -r reply <&"$reply_fd"
  test "$reply" = PONG
}

read_result() {
  local result
  read -r result <&"$reply_fd"
  test "$result" = "$1"
}

send_command "resolve_flake_dir:$FLAKE_DIR"
read_result "$FLAKE_DIR"

send_command "is_dir_flake_exists"
read_result OK

send_command "is_flake_file_exists"
read_result OK

send_command "setup_tmp_lock"
read_result OK

test ! -e "$STAGING_DIR/flake.nix"
test -f "$STAGING_DIR/flake.lock"
test "$(cat "$STAGING_DIR/flake.lock")" = "original lock"
test -f "$FLAKE_DIR/.git/keep"
test ! -e "$FLAKE_DIR/.worker-show"
test ! -e "$FLAKE_DIR/.worker-update"
test ! -e "$FLAKE_DIR/.worker-build"

send_command "check_nixos_config"
read_result OK
test -f "$FLAKE_DIR/.worker-show"


send_command "update_lock_file"
read_result OK
test "$(cat "$STAGING_DIR/flake.lock")" = "updated lock"
test -f "$FLAKE_DIR/.worker-update"

readonly CONFIG="$FLAKE_DIR#nixosConfigurations.test.config.system.build.toplevel"
send_command "build:$CONFIG"
read_result OK
read_result "$STAGING_DIR/closure"

test -f "$FLAKE_DIR/.worker-build"

send_command exit
read_result EXIT

exec {cmd_fd}>&-
exec {reply_fd}<&-
wait "$worker_pid"
worker_pid=""

printf 'ok: Git flake source semantics\n'
