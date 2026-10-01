#!/usr/bin/env bash

set -o errexit
set -o nounset
set -o pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly PROJECT_ROOT
readonly NVD_FILE="$PROJECT_ROOT/src/lib/nvd.py"

export NAME=nixos-upgrade-test
python3 - "$NVD_FILE" <<'PY'
import pathlib
import sys

library_dir = pathlib.Path(sys.argv[1]).parent
sys.path.insert(0, str(library_dir))
import nvd

configuration_diff = "configuration changed from [old-value] to [new-value]"
assert nvd.count_changes(configuration_diff).total == 0

package_diff = "\033[32m[U*]\033[0m package 1.0 -> 2.0"
assert nvd.count_changes(package_diff).total == 1

print("ok: package change detection uses nvd status markers")
PY
