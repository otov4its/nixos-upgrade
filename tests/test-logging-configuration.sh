#!/usr/bin/env bash

set -o errexit
set -o nounset
set -o pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly PROJECT_ROOT
readonly PYTHON_FILE="$PROJECT_ROOT/src/lib/nixos-upgrade.py"

export NAME=nixos-upgrade-test
python3 - "$PYTHON_FILE" <<'PY'
import importlib.util
import io
import logging
import os
import pathlib
import sys
import types

module_path = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(module_path.parent))
spec = importlib.util.spec_from_file_location("nixos_upgrade", module_path)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

options = module.cli_options.parse_args([], module.get_runtime_defaults())
logging.raiseExceptions = not __debug__
module.console_module.Console(options, stderr=io.StringIO())

assert logging.raiseExceptions is __debug__
print("ok: logging.raiseExceptions is configured in the logging module")
PY
