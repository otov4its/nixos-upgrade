#!/usr/bin/env bash

set -o errexit
set -o nounset
set -o pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly PROJECT_ROOT

if (($# == 0)); then
  DEFAULT_OUT="$(nix build --no-link --print-out-paths "$PROJECT_ROOT#default")"
  DEV_OUT="$(nix build --no-link --print-out-paths "$PROJECT_ROOT#dev")"
elif (($# == 2)); then
  DEFAULT_OUT=$1
  DEV_OUT=$2
else
  printf 'usage: %s [DEFAULT_OUT DEV_OUT]\n' "${BASH_SOURCE[0]}" >&2
  exit 2
fi
readonly DEFAULT_OUT DEV_OUT

test -d "$DEFAULT_OUT/lib"
test -d "$DEV_OUT/lib"

if ! find "$DEFAULT_OUT/lib" -type f -name '*.opt-2.pyc' -print -quit | grep --quiet .; then
  printf 'default package is missing optimized Python bytecode\n' >&2
  exit 1
fi

if find "$DEV_OUT/lib" -type f -name '*.pyc' -print -quit | grep --quiet .; then
  printf 'dev package unexpectedly contains unused Python bytecode\n' >&2
  exit 1
fi

printf 'ok: default package has optimized bytecode; dev package does not compile bytecode\n'
