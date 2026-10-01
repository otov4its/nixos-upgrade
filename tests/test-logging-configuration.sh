#!/usr/bin/env bash

set -o errexit
set -o nounset
set -o pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly PROJECT_ROOT
readonly PYTHON_FILE="$PROJECT_ROOT/src/lib/nixos-upgrade.py"

export NAME=nixos-upgrade-test
python3 - "$PYTHON_FILE" <<'PY'
import io
import logging
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(sys.argv[1]).parent))
import cli_options
import console

runtime_defaults = cli_options.RuntimeDefaults(
    name=os.environ["NAME"],
    version="test-version",
    hostname="test-host",
    default_flake=pathlib.Path("/etc/nixos"),
)
options = cli_options.parse_args([], runtime_defaults)
logging.raiseExceptions = not __debug__
console.Console(options, stderr=io.StringIO())

assert logging.raiseExceptions is __debug__
print("ok: logging.raiseExceptions is configured in the logging module")
PY
