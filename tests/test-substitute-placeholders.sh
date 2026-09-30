#!/usr/bin/env bash

set -o errexit
set -o nounset
set -o pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly PROJECT_ROOT
readonly PACKAGE_NIX="$PROJECT_ROOT/package.nix"

if grep --extended-regexp --quiet -- '--replace([[:space:]]|$)' "$PACKAGE_NIX"; then
  printf 'package.nix contains deprecated --replace directives\n' >&2
  exit 1
fi

replace_fail_count="$(grep --extended-regexp --count -- '--replace-fail([[:space:]]|$)' "$PACKAGE_NIX" || true)"
if [[ "$replace_fail_count" -ne 11 ]]; then
  printf 'expected 11 --replace-fail directives, found %s\n' "$replace_fail_count" >&2
  exit 1
fi

if grep --extended-regexp --quiet -- 'privileged-worker|@worker@' "$PACKAGE_NIX"; then
  printf 'package.nix still references the persistent privileged worker\n' >&2
  exit 1
fi

for placeholder in \
  '@name@' \
  '@version@' \
  '@description@' \
  '@path@' \
  '@helper@' \
  '@pyfile@' \
  '@py_opts@'; do
  if ! grep --fixed-strings --quiet -- "--replace-fail \"$placeholder\"" "$PACKAGE_NIX"; then
    printf 'missing --replace-fail directive for placeholder %s\n' "$placeholder" >&2
    exit 1
  fi
done

if ! grep --fixed-strings --quiet -- "--replace-fail \"@path@\" \"\${lib.makeBinPath runtimeInputs}\"" "$PACKAGE_NIX"; then
  printf 'runtime inputs must be embedded in the launcher PATH\n' >&2
  exit 1
fi

if grep --fixed-strings --quiet -- 'buildInputs = runtimeInputs;' "$PACKAGE_NIX"; then
  printf 'runtime inputs should not be added as build inputs\n' >&2
  exit 1
fi

printf 'ok: package placeholders use --replace-fail and runtime inputs stay in launcher PATH\n'
