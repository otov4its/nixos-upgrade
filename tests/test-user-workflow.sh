#!/usr/bin/env bash

set -o errexit
set -o nounset
set -o pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly PROJECT_ROOT
readonly PYTHON_FILE="$PROJECT_ROOT/src/lib/nixos-upgrade.py"
TEST_ROOT="$(mktemp --directory /tmp/nixos-upgrade-workflow-test.XXXXXXXXXX)"
readonly TEST_ROOT
readonly RUNTIME_DIR="$TEST_ROOT/runtime"
readonly FAKE_BIN="$TEST_ROOT/fake bin"
readonly FLAKE_DIR="$TEST_ROOT/flake source"
readonly CURRENT_SYSTEM="$TEST_ROOT/current system"
readonly NEW_SYSTEM="$TEST_ROOT/new system"
readonly EVENT_LOG="$TEST_ROOT/events"

readonly SUDO_ARGS="$TEST_ROOT/sudo-args"
readonly SUDO_INPUT="$TEST_ROOT/sudo-input.json"
readonly SUDO_OPTIONS="$TEST_ROOT/sudo-options.json"
readonly FAKE_SUDO="$FAKE_BIN/sudo"
readonly FAKE_HELPER="$TEST_ROOT/activate helper"

cleanup() {
  rm --recursive --force "$TEST_ROOT"
}
trap cleanup EXIT

write_fake_command() {
  local command_path=$1
  printf '#!%s\n' "$BASH" >"$command_path"
  cat >>"$command_path"
  chmod +x "$command_path"
}

mkdir --parents "$RUNTIME_DIR" "$FAKE_BIN" "$FLAKE_DIR" "$CURRENT_SYSTEM" "$NEW_SYSTEM"
printf 'flake source\n' >"$FLAKE_DIR/flake.nix"
printf 'original lock\n' >"$FLAKE_DIR/flake.lock"

write_fake_command "$FAKE_BIN/nix" <<'EOF'
set -o errexit
set -o nounset
set -o pipefail

printf 'nix' >> "$TEST_EVENT_LOG"
for arg in "$@"; do
  printf '\t%s' "$arg" >> "$TEST_EVENT_LOG"
done
printf '\n' >> "$TEST_EVENT_LOG"

[[ "$1" == --extra-experimental-features && "$2" == 'nix-command flakes' ]]
shift 2

if [[ "$1" == flake && "$2" == update ]]; then
  [[ "${TEST_EXPECT_NO_UPDATE:-0}" == 0 ]]
  shift 2
  flake_dir=
  lock_file=
  inputs=()
  while (($#)); do
    case "$1" in
      --flake)
        flake_dir=$2
        shift 2
        ;;
      --output-lock-file)
        lock_file=$2
        shift 2
        ;;
      *)
        inputs+=("$1")
        shift
        ;;
    esac
  done
  expected_inputs=()
  if [[ -n "${TEST_EXPECTED_INPUTS:-}" ]]; then
    read -r -a expected_inputs <<< "$TEST_EXPECTED_INPUTS"
  fi
  [[ "${inputs[*]}" == "${expected_inputs[*]}" ]]
  [[ "$flake_dir" == "$TEST_FLAKE_DIR" ]]
  [[ "$lock_file" != "$TEST_FLAKE_DIR/flake.lock" ]]
  [[ -n "$lock_file" ]]
  printf 'updated lock\n' > "$lock_file"
  exit 0
fi

if [[ "$1" == flake && "$2" == show ]]; then
  printf 'nix flake show must not run\n' >&2
  exit 82
fi

if [[ "$1" == build ]]; then
  shift
  out_link=
  reference_lock=
  no_write=false
  config=
  while (($#)); do
    case "$1" in
      --out-link)
        out_link=$2
        shift 2
        ;;
      --reference-lock-file)
        reference_lock=$2
        shift 2
        ;;
      --no-write-lock-file)
        no_write=true
        shift
        ;;
      *)
        config=$1
        shift
        ;;
    esac
  done
  [[ "$no_write" == true ]]
  if [[ -n "${TEST_EXPECTED_CONFIGURATION:-}" ]]; then
    [[ "$config" == "$TEST_FLAKE_DIR#nixosConfigurations.$TEST_EXPECTED_CONFIGURATION.config.system.build.toplevel" ]]
  else
    [[ "$config" == "$TEST_FLAKE_DIR"#nixosConfigurations.* ]]
  fi
  [[ -n "$reference_lock" && -f "$reference_lock" ]]
  if [[ "${TEST_EXPECT_NO_UPDATE:-0}" == 1 ]]; then
    [[ "$(cat "$reference_lock")" == 'original lock' ]]
  else
    [[ "$(cat "$reference_lock")" == 'updated lock' ]]
  fi
  if [[ "${TEST_BUILD_FAILURE:-0}" == 1 ]]; then
    exit 31
  fi
  if [[ "${TEST_REMOVE_LOCK_AFTER_BUILD:-0}" == 1 ]]; then
    rm -- "$reference_lock"
  fi
  [[ -n "$out_link" ]]
  ln --symbolic -- "$TEST_BUILD_TARGET" "$out_link"
  exit 0
fi

printf 'unexpected fake nix invocation\n' >&2
exit 83
EOF

write_fake_command "$FAKE_BIN/nvd" <<'EOF'
set -o errexit
set -o nounset
set -o pipefail
[[ "$1" == --color=always ]]
[[ "$2" == diff ]]
printf 'nvd\n' >> "$TEST_EVENT_LOG"
printf 'Comparing system closures\nPackages\n[A.] added-package\n'
EOF

write_fake_command "$FAKE_SUDO" <<'EOF'
set -o errexit
set -o nounset
set -o pipefail
printf 'sudo\n' >> "$TEST_EVENT_LOG"
printf '%s\0' "$@" > "$TEST_SUDO_ARGS"
cat > "$TEST_SUDO_INPUT"
if [[ "${TEST_SUDO_DENIED:-0}" == 1 ]]; then
  exit 1
fi
printf '%s\n' "$TEST_SUDO_RESULT"
EOF

export PATH="$FAKE_BIN:$PATH"
export TEST_EVENT_LOG="$EVENT_LOG"
export TEST_FLAKE_DIR="$FLAKE_DIR"
export TEST_SUDO_ARGS="$SUDO_ARGS"
export TEST_SUDO_INPUT="$SUDO_INPUT"
export TEST_SUDO_OPTIONS="$SUDO_OPTIONS"
export NAME=nixos-upgrade-test
export TERM_CORE_SIGS='2 15'
export XDG_RUNTIME_DIR="$RUNTIME_DIR"

run_app() {
  python3 - "$PYTHON_FILE" "$CURRENT_SYSTEM" "$FAKE_SUDO" "$FAKE_HELPER" "$@" <<'PY'
import importlib.util
import json
import os
import pathlib
import sys

python_file = pathlib.Path(sys.argv[1])
current_system, fake_sudo, fake_helper = sys.argv[2:5]
args = sys.argv[5:]
sys.path.insert(0, str(python_file.parent))
import activation

spec = importlib.util.spec_from_file_location("nixos_upgrade", python_file)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

activation.DEFAULT_SUDO_PATH = fake_sudo
activation.HELPER_PATH = fake_helper
module.CliProgram.get_current_system_closure = lambda self: current_system
runner_class = module.command_runner.CommandRunner
original_run = runner_class.run

def record_activation_options(runner, command, **kwargs):
    if fake_sudo in command:
        pathlib.Path(os.environ["TEST_SUDO_OPTIONS"]).write_text(json.dumps({
            "argv": command,
            "start_new_session": kwargs.get("start_new_session", True),
            "stderr_is_inherited": (
                kwargs.get("output_policy")
                is module.command_runner.OutputPolicy.INHERIT_STDERR
            ),
            "stdin_data_is_present": kwargs.get("stdin_data") is not None,
            "log_command": kwargs.get("log_command"),
            "log_failure": kwargs.get("log_failure"),
        }))
    return original_run(runner, command, **kwargs)

runner_class.run = record_activation_options
sys.argv = [str(python_file), *args]
module.CliProgram().main()
PY
}

reset_records() {
  : >"$EVENT_LOG"
  rm -f -- "$SUDO_ARGS" "$SUDO_INPUT" "$SUDO_OPTIONS"
}

export TEST_BUILD_TARGET="$NEW_SYSTEM"
export TEST_EXPECT_NO_UPDATE=0
export TEST_BUILD_FAILURE=0
export TEST_SUDO_DENIED=0
export TEST_SUDO_RESULT='{"system":"switched","lock":"failed","commit":"failed"}'
reset_records
if TEST_EXPECTED_CONFIGURATION=test-configuration \
  TEST_EXPECTED_INPUTS='nixpkgs home-manager' run_app \
  --flake "$FLAKE_DIR" -C test-configuration \
  --inputs nixpkgs home-manager --assume-no --color=never \
  >"$TEST_ROOT/decline.out" 2>"$TEST_ROOT/decline.err"; then
  :
else
  status=$?
  cat "$TEST_ROOT/decline.err" >&2
  printf 'declined workflow exited with status %s\n' "$status" >&2
  exit 1
fi
test ! -e "$SUDO_ARGS"
test "$(cat "$FLAKE_DIR/flake.lock")" = 'original lock'
test "$(grep --count '^nix' "$EVENT_LOG")" -eq 2
grep -Fq 'nvd' "$EVENT_LOG"
if [[ "${TEST_GIT_FLAKE_ONLY:-0}" == 1 ]]; then
  printf 'ok: Nix commands use the raw flake path and isolated lock files\n'
  exit 0
fi

reset_records
run_app --flake "$FLAKE_DIR" --assume-yes --commit-message $'operator note\nsecond line' --color=never \
  >"$TEST_ROOT/confirmed.out" 2>"$TEST_ROOT/confirmed.err"

python3 - "$EVENT_LOG" "$SUDO_ARGS" "$SUDO_INPUT" "$SUDO_OPTIONS" "$FLAKE_DIR" "$FAKE_HELPER" <<'PY'
import base64
import json
import pathlib
import sys

events_path, args_path, input_path, options_path, flake_dir, helper = map(pathlib.Path, sys.argv[1:])
events = [line.split("\t", 1)[0] for line in events_path.read_text().splitlines()]
assert events == ["nix", "nix", "nvd", "sudo"], events
args = args_path.read_bytes().split(b"\0")[:-1]
args = [arg.decode() for arg in args]
assert args[0] == "--", args
assert args[1:4] == [str(helper), "activate", "--expected-current"], args
assert "-c" not in args and "sh" not in args, args
assert args[args.index("--flake-dir") + 1] == str(flake_dir), args
manifest = json.loads(input_path.read_text())
assert set(manifest) == {"lock_file_base64", "commit_message_base64"}, manifest
assert base64.b64decode(manifest["lock_file_base64"], validate=True) == b"updated lock\n"
commit_message = base64.b64decode(manifest["commit_message_base64"], validate=True).decode()
assert "operator note\nsecond line" in commit_message, commit_message
assert "added-package" in commit_message, commit_message
options = json.loads(options_path.read_text())
assert options["argv"][0].endswith("/env"), options
assert options["start_new_session"] is False, options
assert options["stderr_is_inherited"], options
assert options["stdin_data_is_present"], options
assert options["log_command"] is False and options["log_failure"] is False, options
PY

grep -Fq 'lock' "$TEST_ROOT/confirmed.err"
grep -Fq 'commit' "$TEST_ROOT/confirmed.err"

reset_records
if TEST_REMOVE_LOCK_AFTER_BUILD=1 run_app --flake "$FLAKE_DIR" --assume-yes --color=never \
  >"$TEST_ROOT/missing-lock.out" 2>"$TEST_ROOT/missing-lock.err"; then
  printf 'missing updated lock file unexpectedly allowed activation\n' >&2
  exit 1
fi
test ! -e "$SUDO_ARGS"
grep -Fq 'could not read temporary lock file' "$TEST_ROOT/missing-lock.err"

reset_records
export TEST_EXPECT_NO_UPDATE=1
export TEST_SUDO_RESULT='{"system":"switched","lock":"not-requested","commit":"not-requested"}'
run_app --flake "$FLAKE_DIR" --no-update-lock-file --assume-yes --no-commit --color=never \
  >"$TEST_ROOT/no-update.out" 2>"$TEST_ROOT/no-update.err"
python3 - "$EVENT_LOG" "$SUDO_ARGS" "$SUDO_INPUT" <<'PY'
import json
import pathlib
import sys

events = pathlib.Path(sys.argv[1]).read_text().splitlines()
assert [line.split("\t", 1)[0] for line in events] == ["nix", "nvd", "sudo"], events
args = [arg.decode() for arg in pathlib.Path(sys.argv[2]).read_bytes().split(b"\0")[:-1]]
assert "--no-commit" in args, args
manifest = json.loads(pathlib.Path(sys.argv[3]).read_text())
assert manifest == {"lock_file_base64": None, "commit_message_base64": None}, manifest
PY

reset_records
export TEST_EXPECT_NO_UPDATE=0
export TEST_BUILD_FAILURE=1
if run_app --flake "$FLAKE_DIR" --assume-yes --color=never \
  >"$TEST_ROOT/build-failure.out" 2>"$TEST_ROOT/build-failure.err"; then
  printf 'build failure unexpectedly succeeded\n' >&2
  exit 1
fi
test ! -e "$SUDO_ARGS"
if grep -Fq 'nvd' "$EVENT_LOG"; then
  printf 'NVD diff ran after a failed build\n' >&2
  exit 1
fi

reset_records
export TEST_BUILD_FAILURE=0
export TEST_BUILD_TARGET="$CURRENT_SYSTEM"
run_app --flake "$FLAKE_DIR" --assume-yes --color=never \
  >"$TEST_ROOT/no-changes.out" 2>"$TEST_ROOT/no-changes.err"
test ! -e "$SUDO_ARGS"
if grep -Fq 'nvd' "$EVENT_LOG"; then
  printf 'NVD diff ran when the closures were identical\n' >&2
  exit 1
fi

if grep --extended-regexp --quiet 'privileged-worker|setpriv|PY_SH_FD|SH_PY_FD|COMMIT_MSG|CMD_IFS|setup_ipc|PONG' \
  "$PROJECT_ROOT/src/bin/nixos-upgrade"; then
  printf 'launcher still contains the worker protocol or privilege drop\n' >&2
  exit 1
fi
for launcher_contract in '@path@' '@name@' '@version@' 'TERM_CORE_SIGS' 'block-signal'; do
  if ! grep --fixed-strings --quiet -- "$launcher_contract" "$PROJECT_ROOT/src/bin/nixos-upgrade"; then
    printf 'launcher is missing required behavior: %s\n' "$launcher_contract" >&2
    exit 1
  fi
done

for python_contract in 'XDG_RUNTIME_DIR' 'nixos-upgrade.lock' 'fcntl.flock'; do
  if ! grep --fixed-strings --quiet -- "$python_contract" "$PYTHON_FILE"; then
    printf 'Python CLI is missing required behavior: %s\n' "$python_contract" >&2
    exit 1
  fi
done

if grep --extended-regexp --quiet 'parse_options|check_singleton|XDG_RUNTIME_DIR|flock|man --pager' \
  "$PROJECT_ROOT/src/bin/nixos-upgrade"; then
  printf 'launcher still parses CLI options or manages the singleton lock\n' >&2
  exit 1
fi

printf 'ok: user workflow keeps Nix unprivileged and elevates only after confirmation\n'
