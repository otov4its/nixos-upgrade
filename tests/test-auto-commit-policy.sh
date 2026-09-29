#!/usr/bin/env bash

set -o errexit
set -o nounset
set -o pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly PROJECT_ROOT
readonly HELPER="$PROJECT_ROOT/src/lib/nixos-upgrade-activate"

commit_block=$(sed -n '/^function commit_repository {$/,/^}$/p' "$HELPER")
owner_block=$(sed -n '/^function run_as_owner {$/,/^}$/p' "$HELPER")

if [[ "$commit_block" != *'commit'* || "$commit_block" != *'--all'* ]]; then
  printf 'auto-commit must include all modified tracked files\n' >&2
  exit 1
fi

if [[ "$commit_block" == *'--allow-empty'* ]]; then
  printf 'auto-commit must not create empty commits\n' >&2
  exit 1
fi

if [[ "$commit_block" != *'--file=-'* ]]; then
  printf 'auto-commit must receive the message on stdin\n' >&2
  exit 1
fi

if [[ "$commit_block" != *'--porcelain --untracked-files=no'* ]]; then
  printf 'auto-commit must detect tracked changes before committing\n' >&2
  exit 1
fi

# These patterns intentionally match literal source text.
# shellcheck disable=SC2016
if [[ "$owner_block" != *'HOME=$home'* || "$owner_block" != *'runuser --user'* ]]; then
  printf 'owner commands must run with the repository owner credentials and HOME\n' >&2
  exit 1
fi

# This pattern intentionally matches literal source text.
# shellcheck disable=SC2016
if ! grep --fixed-strings --quiet -- 'lookup_owner_account "$git_uid"' "$HELPER"; then
  printf 'auto-commit must select the Git-directory owner\n' >&2
  exit 1
fi

printf 'ok: auto-commit preserves tracked changes and Git-directory owner identity\n'
