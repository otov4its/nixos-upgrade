#!/usr/bin/env bash

set -o errexit
set -o nounset
set -o pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly PROJECT_ROOT
readonly PYTHON_FILE="$PROJECT_ROOT/src/lib/nixos-upgrade.py"

NAME=nixos-upgrade-test \
CMD_IFS=: \
PY_SH_FD=3 \
SH_PY_FD=4 \
COMMIT_MSG_W_FD=5 \
TMP_DIR=/tmp \
python3 - "$PYTHON_FILE" <<'PY'
import importlib.util
import pathlib
import sys

module_path = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(module_path.parent))
spec = importlib.util.spec_from_file_location("nixos_upgrade", module_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

program = object.__new__(module.CliProgram)

program.diff = "configuration changed from [old-value] to [new-value]"
assert not program.has_pkgs_changes()

program.diff = "\033[32m[U*]\033[0m package 1.0 -> 2.0"
assert program.has_pkgs_changes()

print("ok: package change detection uses nvd status markers")
PY
