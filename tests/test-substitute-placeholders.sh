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
if [[ "$replace_fail_count" -ne 10 ]]; then
  printf 'expected 10 --replace-fail directives, found %s\n' "$replace_fail_count" >&2
  exit 1
fi

for placeholder in \
  '@name@' \
  '@version@' \
  '@description@' \
  '@man@' \
  '@path@' \
  '@worker@' \
  '@pyfile@' \
  '@py_opts@'
do
  if ! grep --fixed-strings --quiet -- "--replace-fail \"$placeholder\"" "$PACKAGE_NIX"; then
    printf 'missing --replace-fail directive for placeholder %s\n' "$placeholder" >&2
    exit 1
  fi
done

printf 'ok: package placeholders use --replace-fail\n'
