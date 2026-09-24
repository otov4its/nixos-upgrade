#!/usr/bin/env bash

set -o errexit
set -o nounset
set -o pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly PROJECT_ROOT
readonly WORKER="$PROJECT_ROOT/src/lib/privileged-worker"

commit_block=$(sed -n '/^    "commit")$/,/^    ;;$/p' "$WORKER")

if [[ "$commit_block" != *'--all'* ]]; then
  printf 'auto-commit must include all modified tracked files\n' >&2
  exit 1
fi

if [[ "$commit_block" == *'--allow-empty'* ]]; then
  printf 'auto-commit must not create empty commits\n' >&2
  exit 1
fi

if [[ "$commit_block" == *'GIT_COMMITTER_EMAIL="<>"'* ]]; then
  printf 'auto-commit must not use an invalid committer email\n' >&2
  exit 1
fi

home_assignment="HOME=\"\$repo_home\""
if [[ "$commit_block" != *"$home_assignment"* ]]; then
  printf 'auto-commit must use the repository owner home directory\n' >&2
  exit 1
fi

if [[ "$commit_block" != *'NO_CHANGES'* ]]; then
  printf 'auto-commit must report when there are no tracked changes\n' >&2
  exit 1
fi

if [[ "$commit_block" != *'ERR_REPO_HOME'* ]]; then
  printf 'auto-commit must report owner home lookup errors through IPC\n' >&2
  exit 1
fi

if [[ "$commit_block" == *'could not determine home directory'* ]]; then
  printf 'auto-commit must not print raw worker diagnostics\n' >&2
  exit 1
fi

printf 'ok: auto-commit policy preserves tracked changes and owner identity\n'
