#!/usr/bin/env bash

set -o errexit
set -o nounset
set -o pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly PROJECT_ROOT
readonly BIN_FILE="$PROJECT_ROOT/src/bin/nixos-upgrade"

function_definition_file=$(mktemp)
readonly function_definition_file
trap 'rm -f "$function_definition_file"' EXIT

awk '
  /^function get_term_core_default_action_sigs \{/ { capture=1 }
  capture { print }
  capture && /^}/ { exit }
' "$BIN_FILE" >"$function_definition_file"

# shellcheck disable=SC1090
source "$function_definition_file"

expected_signals=(
  SIGHUP SIGINT SIGQUIT SIGUSR1 SIGUSR2 SIGTERM SIGPIPE SIGXCPU SIGXFSZ SIGALRM
)
expected=()
for signal_name in "${expected_signals[@]}"; do
  expected+=("$(kill -l "$signal_name")")
done

actual=$(get_term_core_default_action_sigs)
expected_string="${expected[*]}"

if [[ "$actual" != "$expected_string" ]]; then
  printf 'expected signals: %s\nactual signals:   %s\n' "$expected_string" "$actual" >&2
  exit 1
fi

printf 'ok: TERM_CORE_SIGS contains the intended signals\n'
